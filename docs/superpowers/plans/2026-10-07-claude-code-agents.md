# Claude Code Agents and Interactive Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A second agent runtime, the Claude Agent SDK (Claude Code), next to the OpenAI Agents SDK agents. A
Claude Code agent is only a registration (name, description, runtime) listed in **All agents** with a **Claude Code
SDK** badge. **Docs → Generate documents → New documentation → With an agent** starts an interactive session: the
admin picks the agent, a Claude model, a documentation prompt, context (code projects, folders under `DATA_ROOT`,
servers as text, notes), comments, a document name and a folder. A new `claude-code-worker` container runs one
`ClaudeSDKClient` per active session with read-only file tools plus Bash over a read-only `DATA_ROOT`. The session
page streams the run over SSE, accepts messages, Stop and Close, shows each finished turn's final answer as the draft,
and **Save document** saves the draft as a versioned document in its folder and exports it. Every event is also an
agent run record (**Activity → Agent runs**). Sessions are listed under **Docs → Sessions**.

**Architecture:**
- **Schema (migration 0070)**: `ka.agents.runtime` (`openai` | `claude_code`) with a check that a Claude Code agent
  is a bare registration; the required system prompt (`agent_system_prompt_fk`) applies to OpenAI agents only, through
  a generated column `prompt_agent`. New tables `ka.agent_sessions_cc` (one row per session: agent, model, prompt and
  context snapshots, status, draft, document, token totals, worker lease) and `ka.agent_session_inputs` (queued user
  messages with `seq`, `status`, the `turn` they were delivered in).
- **Shared rules (`knowledge_llm.claude_model`, `knowledge_admin.claude_code`, `knowledge_admin.data_root.data_folder`)**:
  what counts as a Claude connection, the fixed tools, the built-in system prompt, the first message and idle closing.
- **admin-api (`knowledge_admin.claude_code_api`, routes under `/api/claude-code`)**: create and rename Claude Code
  agents; create a session (validates agent, model, prompt, context, document name and folder; snapshots the context
  as absolute container paths; queues the first message as input 1); list, detail, send message, stop, close; an SSE
  stream that polls `ka.agent_run_events` by id (`cc:<session>:<turn>` run keys) and the session row; save document
  through `save_document` and `export_document`. Existing OpenAI-only places refuse or hide Claude Code agents
  (`get_agent_config`, profile edit and model, versions, Reader candidates, board submit, `knowledge_llm.profiles`).
- **Worker (`knowledge_admin.claude_code_worker`, script `knowledge-claude-code-worker`)**: claims sessions that have
  queued input or an interrupted turn (`FOR UPDATE SKIP LOCKED`, 60 s lease renewed every poll), runs one asyncio task
  per session with one `ClaudeSDKClient` (created on the first turn, kept until the session closes or idles 5 minutes,
  then resumed with the CLI session id). Each turn sends the queued messages, records every SDK message as run events,
  answers Stop and Close with `interrupt()`, and stores the turn's final answer as the draft. The model key is decrypted
  in the worker and passed only in the CLI process environment. The CLI runs as the unprivileged `claude` user through
  `knowledge-claude-cli` (`ClaudeAgentOptions.cli_path`), so Bash cannot read the worker's environment.
- **Container (`deploy/compose.yaml`)**: service `claude-code-worker` built from `Dockerfile.admin-api`, `DATA_ROOT`
  read-only, a named volume `claude-code-home` at `/home/claude` as its only writable mount, read-only root filesystem,
  `cap_drop: [ALL]`, `cap_add: [SETUID, SETGID]`, default network (Postgres and the model provider).
- **admin-ui**: Agents list badge and **New Claude Code agent**; a Claude Code agent page with only name and
  description; the board hides Claude Code agents; **With an agent** scope with `ClaudeCodeSessionForm`;
  `ClaudeCodeSessionView` (`/documentation/sessions/:sessionId`, EventSource stream, Send message, Stop, Close session,
  draft as Markdown, Save document with same-name confirmation); `ClaudeCodeSessionsView` (`/documentation/sessions`)
  and **Docs → Sessions** in the sidebar.

**Tech Stack:**
- PostgreSQL 17 with pgvector, golang-migrate SQL migrations, `db/tests/run.sh`;
- FastAPI, pydantic v2, psycopg3 (`services/admin-api`), pytest against a disposable PostgreSQL container
  (`knowledge-agent-v3-postgres:17.10-vector0.8.6`);
- `claude-agent-sdk==0.2.163` (PyPI, Python ≥ 3.10, manylinux x86_64/aarch64 and macOS wheels that bundle Claude Code
  CLI 2.1.286 in `claude_agent_sdk/_bundled/claude`; no Node.js);
- Docker Compose (`docker compose config` rendering in `tests/test_setup.py`);
- React Router 7 with shadcn/ui, Vitest with Testing Library (`services/admin-ui`).

**Spec:** `docs/superpowers/specs/2026-10-07-claude-code-agents-design.md` (binding; includes the update that a Claude
Code agent is only a registration and the session form's Model is required).

## Global Constraints

- Implementers and reviewers never run make setup/compose-validate/up/smoke and never touch .env or .local; compose checks use a temp .env.
- Model credentials are never logged, written to disk or included in events.
- Runtime: `ka.agents.runtime text NOT NULL DEFAULT 'openai' CHECK (runtime IN ('openai','claude_code'))`. Check
  `claude_code_agent_is_a_registration`: `runtime = 'openai' OR (NOT is_coordinator AND model_id IS NULL AND
  active_config_version_id IS NULL AND NOT documents_enabled AND NOT documents_grounded AND NOT repositories_enabled AND
  NOT ssh_enabled AND NOT skills_enabled AND NOT mcp_enabled AND NOT services_enabled AND NOT all_collections AND NOT
  all_repositories)`. `agent_system_prompt_fk` becomes `FOREIGN KEY (prompt_agent) REFERENCES agent_prompts(agent)
  DEFERRABLE INITIALLY DEFERRED` with `prompt_agent text GENERATED ALWAYS AS (CASE WHEN runtime = 'openai' THEN id END)
  STORED`. Claude Code agents have no `agent_prompts`, `agent_versions`, `agent_configs` or `operations_settings` rows.
- Claude connection (`knowledge_llm.claude_model`, mirrored by `claudeModel` in `lib/claudeCode.ts`):
  `api_format == 'native' and (provider == 'anthropic' or provider == 'bedrock' and 'anthropic.claude' in model)`.
  Refusal text (400, agent sessions): `Select an enabled Claude model (Bedrock or Anthropic) from LLM Settings.`
- Hidden from OpenAI-only places: `get_agent_config` raises `ValueError(CLAUDE_CODE_ONLY)` with
  `CLAUDE_CODE_ONLY = 'Claude Code agents run in documentation sessions only (Docs → Generate documents → With an agent).'`
  (test chats and versions answer 409); `PUT /api/agents/{id}` → 409 `Claude Code agents have only a name and a
  description; edit them on their agent page.`; `PUT /api/agents/{id}/model` → 409 `Claude Code agents choose their
  model when a session starts.`; versions → 409 `Claude Code agents have no configuration versions.`; Reader candidates
  only `runtime='openai'`; board submit → 400 `Claude Code agents cannot join the multi-agent board.`;
  `knowledge_llm.profiles.profile(..., runnable=True)` raises `ModelUnavailable`. Deleting an agent with sessions → 409
  (the existing retained-history message).
- Built-in system prompt (appended to Claude Code's preset, `{'type': 'preset', 'preset': 'claude_code', 'append':
  SYSTEM_PROMPT}`): `You are an expert technical writer. You explore the provided folders and context to write accurate
  documentation; everything you state must be grounded in what you read.`
- Tools, fixed: `tools` = `allowed_tools` = `['Read', 'Grep', 'Glob', 'Bash']`; `disallowed_tools` = `['Edit',
  'Write', 'NotebookEdit', 'Agent', 'Task', 'Skill', 'WebFetch', 'WebSearch', 'TodoWrite']`; `permission_mode='dontAsk'`;
  `setting_sources=[]` (no `.claude/` settings, hooks or CLAUDE.md from the folders); `skills=[]`; `mcp_servers={}` with
  `strict_mcp_config=True`. No `max_turns` and no `max_budget_usd`: a turn runs until the agent finishes or Stop.
  `cwd` = the first folder, `add_dirs` = every folder, `model` = the connection's model ID, `resume` = the stored CLI
  session id (or `None`), `cli_path` = `$CLAUDE_CODE_CLI` when set.
- CLI environment (`options['env']`): `BASE_ENV = {'HOME': '/home/claude', 'TMPDIR': '/home/claude/tmp',
  'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1', 'DISABLE_AUTOUPDATER': '1', 'DISABLE_TELEMETRY': '1',
  'DISABLE_ERROR_REPORTING': '1'}` plus, for Bedrock, `CLAUDE_CODE_USE_BEDROCK='1'`, `AWS_REGION=<aws_region>`,
  `AWS_BEARER_TOKEN_BEDROCK=<key>`, `ANTHROPIC_MODEL=<model>`; for Anthropic, `ANTHROPIC_API_KEY=<key>`,
  `ANTHROPIC_MODEL=<model>` and `ANTHROPIC_BASE_URL=<base_url without a trailing /v1>`. The worker removes
  `CREDENTIAL_ENCRYPTION_KEY` and `PGPASSWORD` from `os.environ` at start (the SDK merges `os.environ` into the CLI
  environment), and the CLI runs as user `claude` (uid 1001) via `knowledge-claude-cli`.
- First message (input `seq` 1, built by `first_message(prompt, comments, context)`): the prompt content (stripped);
  then, when comments are not blank, `## Comments for this run\n\n<comments>`; then `## Context` with `Working folders
  (read-only; the first one is your working directory):` and one line per folder (`- <path> (code project <name>)` or
  `- <path>`), `Servers (for reference only; you have no access to them):` with `- <name> (<hostname>): <description>`
  (no `: …` when empty) when servers were chosen, `Notes:` with the notes when not blank, and finally `FINAL_ANSWER =
  'When you finish, reply with the complete document in Markdown as your final answer: it becomes the draft. After each
  later message, reply again with the complete updated document.'`. Sections are joined by blank lines.
- Context snapshot: `{'folders': [{'path', 'kind': 'code_project', 'name', 'code_project_id'} …, {'path', 'kind':
  'folder', 'name'} …], 'code_project_ids': [...], 'servers': [{'id', 'name', 'hostname', 'description'}], 'notes'}`.
  Code project folders come first in the chosen project order (`CODE_ROOT/<folder path>`), then `DATA_ROOT` folders
  (`data_path(<folder>)`), duplicates removed keeping the first. Every folder must be an existing directory at creation
  (400 `The folder <path> does not exist. Remove it from the context.`) and when the worker connects (turn fails with
  `The folder <path> no longer exists. Start a new session without it.`).
- Folder under `DATA_ROOT` (`data_folder`): RAG's rule `[A-Za-z0-9_-][A-Za-z0-9._-]*(/[A-Za-z0-9_-][A-Za-z0-9._-]*)*`,
  at most 500 characters; text `DATA_FOLDER_RULE = "A folder is a path under DATA_ROOT such as code/payments or jira:
  names of letters, digits, '.', '_' and '-' separated by '/', none starting with '.'; at most 500 characters."`.
- Session input (`POST /api/claude-code/sessions`): `agent_id`, `model_id`, `prompt_id`, `prompt_version_id` required;
  `comments` ≤ 20,000 and `notes` ≤ 20,000 characters without NUL; `code_project_ids` ≤ 20, `folders` ≤ 20,
  `server_ids` ≤ 50, each unique; at least one code project or folder (`Add at least one code project or folder as
  context`); `document_name` ≤ 240, trimmed, with letters or digits; `folder` follows the documentation folder rule
  (`documentation_folder`, lowercase). Errors: agent not an enabled Claude Code agent → 400 `Select an enabled Claude
  Code agent.`; not a Claude model → 400 (text above); prompt not active → 400 `Select an active documentation
  prompt.`; prompt version changed → 409 `PROMPT_CHANGED`; unavailable code projects or servers → 400.
- Session statuses: `starting` (created, not yet started by a worker), `working`, `waiting` (turn finished, waiting
  for you), `stopped`, `failed`, `closed`. UI labels: Starting · Working · Waiting for you · Stopped · Failed ·
  Finished. `status_message` texts: stop → `Stopped. Send a message to continue.`; worker restart during a turn →
  `The worker restarted during this turn. Send a message to continue.`; idle → `Closed after 24 hours without
  activity.`; close → `Closed.`; failure → `safe_error(...)`.
- Input delivery: a message is always stored as `queued`. The worker delivers all queued messages of a session when
  no turn is running: at the start, and right after the current response (its `ResultMessage`) completes. Several
  queued messages are joined with a blank line into one turn. Each input is delivered once (`status='delivered'`,
  `turn`, `delivered_at`), in `seq` order, in the same transaction that sets the session `working` and `turns =
  turn`. Stop and Close mark the session's queued messages `discarded`.
- Stop: only while `working` (else 409 `The agent is not working, so there is nothing to stop.`); sets
  `stop_requested_at`; the worker calls `interrupt()` within one poll; the turn ends `stopped`. If no `ResultMessage`
  arrives 30 s after `interrupt()`, the worker disconnects the client. Close: sets `closed` (idempotent) and the worker
  interrupts a running turn, disconnects and releases the session. Messages to a closed session → 409 `This session is
  closed. Start a new session.`.
- Idle: sessions in `starting`, `waiting`, `stopped` or `failed` whose `last_activity_at` is older than 24 hours are
  closed (`close_idle`, run by the session list and every worker poll); `working` sessions are never closed by idleness.
- Run records: run key `cc:<session id>:<turn>`; `agent_id` = the Claude Code agent; `actor_id` = the session's
  creator. Per turn: `start` `{kind: 'claude_code', title, agent, turn}`; on turn 1 `instructions` `{agent, text:
  SYSTEM_PROMPT}`; `input` `{task}`; then per SDK message `reasoning` `{text}` (ThinkingBlock), `message` `{text}`
  (TextBlock), `tool_call` `{name, call_id, arguments, summary}` (summary: Read → `file_path`; Grep/Glob → `pattern` or
  `<pattern> in <path>`; Bash → `command`), `tool_result` `{name, call_id, output, preview (first 20 lines),
  is_error}`, `usage` `{input_tokens (input + cache read + cache creation), output_tokens, cached_tokens (cache read),
  total_tokens, cost_usd}` (from `ResultMessage`), `error` `{message}`; `end` `{status: completed | stopped | failed}`.
  Every payload goes through `RunRecorder` redaction with the model key.
- SSE (`GET /api/claude-code/sessions/{id}/events`, `text/event-stream`, `Cache-Control: no-store`, `X-Accel-Buffering:
  no`): `id: <event id>` + `event: run_event` + `data: {id, turn, seq, kind, payload, created_at}` (a `tool_result`
  payload without `output`), `event: session` + the session state when it changes (`status, status_message, turns,
  input_tokens, output_tokens, cached_tokens, cost_usd, draft, draft_turn, document_id, worker_active, queued`),
  `: keepalive` each poll, `event: closed` then end of stream when the session is closed, `event: unavailable` on a
  database error. Resumes after `Last-Event-ID` (or `?after=`). Poll interval `STREAM_POLL_SECONDS = 1.0`.
- Draft: the `ResultMessage.result` of a turn that ended `waiting` (fallback: its last non-empty TextBlock), redacted;
  stopped or failed turns keep the previous draft.
- Save document (`POST /api/claude-code/sessions/{id}/document {update_existing: bool}`): no draft → 409 `There is no
  draft yet. Wait for the agent to finish a turn.`; slug `document_slug(document_name)` in the session folder; an
  existing document with that folder and slug without `update_existing` → 409 `SAME_DOCUMENT_NAME`; saved with
  `save_document(..., folder=<session folder>)`, title = document name, `purpose='code'` when the context has code
  projects else `'explanation'`, `code_project_ids` = the session's still-available code projects, `origin='manual'`
  (no generation row); exported with `export_document(**export_args(export_input(conn, id)))` to
  `<DOCUMENT_OUTPUT_ROOT>/<folder>/<slug>/README.md`; export failure keeps the saved version and returns
  `export_error`. The session's `document_id` is set. Nothing is saved automatically.
- Worker: lease 60 s (`LEASE_SECONDS`), renewed every poll (1 s) for the sessions it runs; claims `status <> 'closed'`
  sessions with no live lease that have a queued input or are `working` (an interrupted turn); at most
  `CLAUDE_CODE_MAX_SESSIONS` (default 4) at once; a session idle with no queued input for `IDLE_RELEASE_SECONDS = 300`
  disconnects its client and is released; the next message resumes it with `resume=<claude_session_id>`.
- Errors shown to people: `safe_error(text, secrets)` = the text redacted with the key, whitespace collapsed, at most
  500 characters (`Claude Code stopped with an error.` when empty), then a hint: contains `inference profile` or
  `on-demand throughput` → `Bedrock needs an inference profile ID for this model, such as
  eu.anthropic.claude-sonnet-4-5-20250929-v1:0. Change the model ID in LLM Settings.`; contains `403`, `401`,
  `accessdenied` or `authentication` → `Check the key and model access of this connection in LLM Settings.`; else
  `Check the model connection in LLM Settings, then send a message to retry.`. Logs carry only session ids, turn
  numbers and exception type names.
- Container `claude-code-worker`: `build: {context: .., dockerfile: deploy/Dockerfile.admin-api}`, `entrypoint:
  [/services/admin-api/.venv/bin/knowledge-claude-code-worker]`, `command: []`, `read_only: true`, `cap_drop: [ALL]`,
  `cap_add: [SETUID, SETGID]`, `security_opt: ["no-new-privileges:true"]`, volumes `*data-root` (read-only) and
  `claude-code-home:/home/claude`, environment `<<: *data-env`, `HOME: /home/claude`, `TMPDIR: /home/claude/tmp`,
  `CLAUDE_CODE_CLI: /services/admin-api/.venv/bin/knowledge-claude-cli`, `PGHOST/PGDATABASE/PGUSER/PGPASSWORD`,
  `CREDENTIAL_ENCRYPTION_KEY`, `AUTH_ISSUER_URL`; default network only; no ports.
- Dependency: `claude-agent-sdk==0.2.163` in `services/admin-api/pyproject.toml`, locked with `uv lock`. No module
  imports `claude_agent_sdk` at import time; only `claude_code_worker.sdk_client` imports it, inside the function.
  Tests use fakes whose class names match the SDK message and block types.
- Migration number 0070 (`0070_claude_code_agents`). `db/tests/run.sh` pins the rolled-back version to `69`. The down
  migration raises `Claude Code agents exist; delete their sessions and agents before rolling back 0070` when any
  `runtime='claude_code'` agent exists.
- UI: sidebar **Docs** order Documents · Prompts · Generate documents · Sessions (`/documentation/sessions`, icon
  `MessagesSquare`) · Folders; session page `/documentation/sessions/<id>`; scope choice label **With an agent**
  (`?scope=agent` preselects it); badge text **Claude Code SDK**.
- Commit trailer: `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Python stays compatible with 3.12.

## Review Focus

1. **The model key or the platform's own secrets leak.** Examples: the Bedrock bearer token in a run event, an SSE
   payload, `status_message`, a log line or the draft; `CREDENTIAL_ENCRYPTION_KEY` or `PGPASSWORD` inherited by the CLI
   (Bash could print them); the CLI running as root and reading the worker's `/proc/1/environ`. The key exists only in
   the worker's memory and the CLI process environment. Pinned in:
   - Task 6: `test_credentials_reach_only_the_cli_environment`;
   - Task 7: `test_no_key_in_events_logs_or_status`;
   - Task 7: `test_worker_strips_database_and_encryption_secrets_from_its_environment`;
   - Task 7: `test_cli_wrapper_drops_to_the_claude_user_before_running_the_bundled_cli`.
2. **A Claude Code agent shows up where OpenAI agents run, or gains OpenAI configuration.** Examples: offered as the
   Reader assistant or a board subagent; a test chat started for it; a model, prompt, version or capability switch
   saved on it; a session started with an OpenAI agent. Pinned in:
   - Task 1: `test_claude_code_agents_are_registrations_only`;
   - Task 2: `test_claude_code_agents_are_hidden_from_openai_only_places`;
   - Task 9: `All agents shows Claude Code agents with their badge and creates one from a name and description`.
3. **The agent gets more access than chosen.** Examples: Edit/Write/MCP/sub-agent tools; `.claude/settings.json` hooks
   or CLAUDE.md from a code project; a folder outside `DATA_ROOT` (`../etc`, `/etc`, `.git`); a writable `DATA_ROOT`;
   SSH credentials for servers that are context only; turn or cost limits that silently cut a run. Pinned in:
   - Task 3: `test_session_input_is_validated`;
   - Task 6: `test_options_allow_only_read_tools_and_bash_in_the_chosen_folders`;
   - Task 8: `test_claude_code_worker_mounts_and_environment`.
4. **Messages, Stop or Close get lost, duplicated or ignored.** Examples: a message sent while working never delivered
   or delivered twice; a message queued before Stop restarting the agent; Close leaving a CLI process running; a
   worker crash leaving a session `working` forever; the stream missing or repeating events after a reconnect. Pinned in:
   - Task 7: `test_queued_message_is_delivered_once_as_the_next_turn`;
   - Task 7: `test_stop_interrupts_the_turn_and_discards_queued_messages`;
   - Task 7: `test_close_ends_the_session_and_releases_the_worker`;
   - Task 7: `test_expired_lease_is_reclaimed_and_the_interrupted_turn_fails`;
   - Task 4: `test_event_stream_replays_history_and_follows_a_fake_worker`.
5. **The wrong text is saved, or saved over another document.** Examples: a stopped turn's partial text becoming the
   draft; an earlier draft replaced by an empty answer; Save overwriting a same-name document without confirmation;
   a document saved outside its folder or not exported; automatic saving. Pinned in:
   - Task 6: `test_turn_messages_become_run_events_and_the_final_answer_is_the_draft`;
   - Task 7: `test_draft_is_the_final_answer_of_each_completed_turn`;
   - Task 5: `test_save_document_needs_confirmation_for_the_same_name_and_exports_into_the_folder`.

## Decisions (where the spec leaves a choice)

- **Runtime column, registration check, prompt FK for OpenAI only.** `ka.agents.runtime` keeps Claude Code agents in
  the same table (names stay unique across runtimes, **All agents** lists both). The 0022 FK made a system prompt
  mandatory for every agent; a generated column that is NULL for Claude Code agents keeps the rule for OpenAI agents
  without placeholder prompts.
- **Hiding is enforced where OpenAI agents are loaded or chosen**, not by filtering every query: `get_agent_config`
  (the one loader for test chats, versions and the Reader check) refuses Claude Code agents; the Reader candidate list,
  board submit, profile edit/model and `knowledge_llm.profiles.profile(runnable=True)` refuse or skip them; the UI
  board list and agent page branch on `runtime`.
- **Agent create/rename has its own routes** (`POST /api/claude-code/agents`, `PUT /api/claude-code/agents/{id}`)
  instead of extending the wizard: the wizard proposes OpenAI configurations. Delete reuses `DELETE /api/agents/{id}`.
  The Agents page keeps **New Agent Wizard** and adds a **New Claude Code agent** button (the spec's "New agent →
  Claude Code agent").
- **Sessions are their own tables**, not `ka.agent_sessions` (that table pins an OpenAI config and history JSON).
- **Input delivery between responses.** `ClaudeSDKClient.query()` must not be called while a response is streaming
  (the SDK expects the previous `ResultMessage` to be drained first), and SDK 0.2.163 has no documented mid-turn
  injection. So a message sent while the agent works is queued and delivered as the next turn right after the current
  response completes ("its next step"). The UI says so. Stop discards queued messages so Stop really stops.
- **One asyncio task per session, the client created lazily and released when idle.** The SDK client must be used in
  the task that connected it: the session task connects, sends and interrupts; a child task only reads
  `receive_response()`. Idle sessions release their client after 5 minutes and resume from the CLI transcript in the
  `claude-code-home` volume (`resume=<session id from ResultMessage/SystemMessage init>`), so `CLAUDE_CODE_MAX_SESSIONS`
  limits running CLIs, not open sessions.
- **Event schema reused from `ka.agent_run_events`** (no new kinds): one run per turn (`cc:<session>:<turn>`), so turns
  appear in **Activity → Agent runs** with status and tokens. The SSE stream polls by event id over the session's turn
  keys (uses the `(run_key, seq)` unique index) and the session row, like the board stream polls status.
- **Running token usage** is the session total updated at the end of each turn from `ResultMessage.usage` (the SDK's
  authoritative per-turn totals); "in" includes cache reads and writes, "cached" is cache reads.
- **Draft = `ResultMessage.result`** of a completed turn; the first message asks the agent to reply with the complete
  document every time, so each completed turn's final answer is a whole draft.
- **Save document reuses `save_document` and the export of code project documents**; it has no generation row, so the
  version's origin is `manual`; the session links to the document. Same-name confirmation reacts to the 409 like the
  code project form.
- **Secrets reach only the CLI.** The worker container runs as root to hold its database and encryption secrets,
  removes them from `os.environ` at start (the SDK passes `os.environ` merged with `options.env` to the CLI), and starts
  the bundled CLI through `knowledge-claude-cli`, which drops to `claude` (setgroups, setgid, setuid) before `execv`.
  The SDK's own `user` option only sets the uid (the gid would stay root), so the wrapper is used through `cli_path`.
- **LS is not a tool in Claude Code 2.1.x**; Glob lists folder contents. The fixed tools are Read, Grep, Glob and Bash.
- **Worker not running**: the session stays `starting`; the page shows `Waiting for the Claude Code worker. The session
  starts when the worker is running.` while no worker holds its lease (`worker_active` false).
- **Concurrency limit** `CLAUDE_CODE_MAX_SESSIONS` (default 4) is operational (CPU/RAM per CLI), not a run limit.
- **Status names.** The spec lists starting, working, waiting for you, finished, stopped and failed, and separately
  Close. Stored statuses are `starting`, `working`, `waiting`, `stopped`, `failed` and `closed`; the UI shows `closed` as
  **Finished** (closed by the user or after 24 idle hours). Any status but `closed` accepts a new message, so a failed
  or stopped turn can be retried in the same conversation.
- **The model is required on the session, never stored on the agent** (spec update): `ka.agents.model_id` stays NULL
  for Claude Code agents (the registration check enforces it) and `ka.agent_sessions_cc.model_id` is NOT NULL. A
  connection edited to a non-Claude model after a session started fails that session's next turn with a safe message.
- **Agent creation refuses a model or instructions** (`extra='forbid'` → 400): the spec's "400 when saving the agent"
  for a non-Claude model becomes "an agent has no model"; the 400 for a non-Claude model applies when a session starts.

## Delivery and test boundaries

Tasks land in order on `feature/claude-code-agents`, and every suite is green after each task:
- Task 1 (migration, `claude_model`) precedes everything: admin-api tests run every migration.
- Task 2 needs Task 1's columns. Tasks 3–5 need Task 2's router. Task 6 has no database. Task 7 needs Tasks 3 and 6.
- Task 8 adds the SDK dependency, the Docker image changes and the compose service; no admin-api test imports the SDK.
- Tasks 9–12 (admin-ui) need the API shapes of Tasks 2–5. Task 13 updates the user guide and runs every suite.

Each task lists the exact tests to run; Task 13 runs every suite.

---

### Task 1: Claude connections and migration 0070

**Files:**
- Modify: `packages/llm-settings/src/knowledge_llm/__init__.py` (add `claude_model`)
- Modify: `services/admin-api/tests/test_llm.py`
- Create: `db/migrations/0070_claude_code_agents.up.sql`
- Create: `db/migrations/0070_claude_code_agents.down.sql`
- Create: `services/admin-api/tests/test_claude_code_migration.py`
- Modify: `db/tests/run.sh` (rollback pin `68` → `69`)

**Interfaces:**
- Produces, in `knowledge_llm`: `claude_model(provider: str, api_format: str, model: str) -> bool`.
- Produces: `ka.agents.runtime`, `ka.agents.prompt_agent`, check `claude_code_agent_is_a_registration`,
  `agent_system_prompt_fk` on `prompt_agent`; tables `ka.agent_sessions_cc`, `ka.agent_session_inputs`.

- [ ] **Step 1: Write the failing tests**

Append to `services/admin-api/tests/test_llm.py`:

```python
def test_claude_model_accepts_only_claude_connections():
    from knowledge_llm import claude_model
    assert claude_model('bedrock', 'native', 'eu.anthropic.claude-sonnet-4-5-20250929-v1:0')
    assert claude_model('anthropic', 'native', 'claude-sonnet-4-5')
    for provider, api_format, model in (('openai', 'responses', 'gpt-5'), ('bedrock', 'native', 'amazon.nova-pro-v1:0'),
                                        ('bedrock', 'embeddings', 'amazon.titan-embed-text-v2:0'),
                                        ('openai', 'chat_completions', 'anthropic.claude-proxy')):
        assert not claude_model(provider, api_format, model)
```

Create `services/admin-api/tests/test_claude_code_migration.py`:

```python
"""0070: Claude Code agents are registrations; their sessions and queued messages have their own tables."""
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0070_claude_code_agents.up.sql', '0070_claude_code_agents.down.sql'


def run(conn, name: str):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def registration(conn, agent_id: str = 'writer', **columns):
    names = ['id', 'name', 'runtime', *columns]
    conn.execute(f"INSERT INTO ka.agents({','.join(names)}) VALUES ({','.join(['%s'] * len(names))})",
                 (agent_id, agent_id.title(), 'claude_code', *columns.values()))


def model(conn, actor):
    return conn.execute("""INSERT INTO ka.llm_models(name,provider,model,api_format,aws_region,api_key_ciphertext,created_by,updated_by)
        VALUES ('Claude','bedrock','eu.anthropic.claude-test','native','eu-central-1',%s,%s,%s) RETURNING id""",
        (b'\x00', actor, actor)).fetchone()['id']


def test_claude_code_agents_are_registrations_only(store):
    with store.connect() as conn:
        registration(conn)  # no system prompt row: the prompt FK applies to OpenAI agents only
        conn.commit()
        assert conn.execute("SELECT runtime,prompt_agent FROM ka.agents WHERE id='writer'").fetchone() == {
            'runtime': 'claude_code', 'prompt_agent': None}
        assert conn.execute("SELECT runtime FROM ka.agents WHERE id='search'").fetchone()['runtime'] == 'openai'
        for column in ('ssh_enabled', 'documents_enabled', 'documents_grounded', 'repositories_enabled', 'skills_enabled',
                       'mcp_enabled', 'services_enabled', 'all_collections', 'all_repositories', 'is_coordinator'):
            with pytest.raises(psycopg.errors.CheckViolation, match='claude_code_agent_is_a_registration'):
                with conn.transaction():
                    registration(conn, 'other', **{column: True})
        actor = store.actor(conn, 'admin-id', 'admin@example.test')
        with pytest.raises(psycopg.errors.CheckViolation, match='claude_code_agent_is_a_registration'):
            with conn.transaction():
                conn.execute("UPDATE ka.agents SET model_id=%s WHERE id='writer'", (model(conn, actor),))
        with pytest.raises(psycopg.errors.CheckViolation):
            with conn.transaction():
                conn.execute("UPDATE ka.agents SET runtime='other' WHERE id='writer'")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):  # OpenAI agents still need a system prompt
        with store.connect() as conn:
            conn.execute("INSERT INTO ka.agents(id,name) VALUES ('missing-prompt','Missing prompt')")


def test_sessions_hold_status_folder_and_ordered_inputs(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        actor = store.actor(conn, 'admin-id', 'admin@example.test')
        registration(conn)
        session = uuid4()
        conn.execute("""INSERT INTO ka.agent_sessions_cc(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,
            context_snapshot,document_name,folder) VALUES (%s,%s,'writer',%s,%s,'Payments','{}','{}','Payments','payments-docs')""",
            (session, project, actor, model(conn, actor)))
        assert conn.execute('SELECT status,turns,draft,input_tokens FROM ka.agent_sessions_cc WHERE id=%s', (session,)).fetchone() == {
            'status': 'starting', 'turns': 0, 'draft': None, 'input_tokens': 0}
        conn.execute("INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text) VALUES (%s,1,%s,'Document it')", (session, actor))
        for statement, error in (
                ("INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text) VALUES (%(s)s,1,%(a)s,'Again')", psycopg.errors.UniqueViolation),
                ("UPDATE ka.agent_session_inputs SET status='delivered' WHERE session_id=%(s)s", psycopg.errors.CheckViolation),
                ("UPDATE ka.agent_session_inputs SET status='later' WHERE session_id=%(s)s", psycopg.errors.CheckViolation),
                ("UPDATE ka.agent_sessions_cc SET status='closed' WHERE id=%(s)s", psycopg.errors.CheckViolation),
                ("UPDATE ka.agent_sessions_cc SET status='busy' WHERE id=%(s)s", psycopg.errors.CheckViolation),
                ("UPDATE ka.agent_sessions_cc SET folder='Payments' WHERE id=%(s)s", psycopg.errors.CheckViolation),
                ("UPDATE ka.agent_sessions_cc SET folder='services/x' WHERE id=%(s)s", psycopg.errors.CheckViolation)):
            with pytest.raises(error):
                with conn.transaction():
                    conn.execute(statement, {'s': session, 'a': actor})
        conn.execute("UPDATE ka.agent_session_inputs SET status='delivered',turn=1,delivered_at=now() WHERE session_id=%s", (session,))
        conn.execute("UPDATE ka.agent_sessions_cc SET status='closed',closed_at=now() WHERE id=%s", (session,))


def test_down_refuses_claude_code_agents_and_restores_the_prompt_rule(store):
    with store.connect() as conn:
        registration(conn)
        conn.commit()
        with pytest.raises(psycopg.errors.RaiseException, match='Claude Code agents exist'):
            run(conn, DOWN)
        conn.rollback()
        conn.execute("DELETE FROM ka.agents WHERE id='writer'")
        conn.commit()
        run(conn, DOWN)
        conn.commit()
        assert conn.execute("""SELECT 1 FROM information_schema.columns WHERE table_schema='ka' AND table_name='agents'
            AND column_name='runtime'""").fetchone() is None
        assert conn.execute("SELECT to_regclass('ka.agent_sessions_cc') AS t").fetchone()['t'] is None
        run(conn, UP)
        conn.commit()
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_claude_code_migration.py tests/test_llm.py::test_claude_model_accepts_only_claude_connections -q`
Expected: FAIL with `ImportError: cannot import name 'claude_model'` and `psycopg.errors.UndefinedColumn: column "runtime" of relation "agents" does not exist`.

- [ ] **Step 3: Implement**

In `packages/llm-settings/src/knowledge_llm/__init__.py`, add after `code_compatible`:

```python
def claude_model(provider: str, api_format: str, model: str) -> bool:
    """A connection the Claude Agent SDK can use: Anthropic's API, or Claude on Bedrock (an inference profile such as
    eu.anthropic.claude-…)."""
    return api_format == 'native' and (provider == 'anthropic' or provider == 'bedrock' and 'anthropic.claude' in model)
```

Create `db/migrations/0070_claude_code_agents.up.sql`:

```sql
SET search_path = ka, public;

-- A Claude Code agent is a registration (name, description, runtime). Model, folders and prompt come with each session.
ALTER TABLE agents ADD COLUMN runtime text NOT NULL DEFAULT 'openai' CHECK (runtime IN ('openai','claude_code'));
ALTER TABLE agents ADD CONSTRAINT claude_code_agent_is_a_registration CHECK (runtime = 'openai' OR (
    NOT is_coordinator AND model_id IS NULL AND active_config_version_id IS NULL
    AND NOT documents_enabled AND NOT documents_grounded AND NOT repositories_enabled AND NOT ssh_enabled
    AND NOT skills_enabled AND NOT mcp_enabled AND NOT services_enabled AND NOT all_collections AND NOT all_repositories));

-- 0022 made a system prompt mandatory for every agent. It stays mandatory for OpenAI agents: the FK now follows a
-- generated column that is NULL for Claude Code agents.
ALTER TABLE agents DROP CONSTRAINT agent_system_prompt_fk;
ALTER TABLE agents ADD COLUMN prompt_agent text GENERATED ALWAYS AS (CASE WHEN runtime = 'openai' THEN id END) STORED;
ALTER TABLE agents ADD CONSTRAINT agent_system_prompt_fk FOREIGN KEY (prompt_agent) REFERENCES agent_prompts(agent)
    DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE agent_sessions_cc (
    id               uuid PRIMARY KEY,
    project_id       uuid NOT NULL REFERENCES projects(id),
    agent_id         text NOT NULL REFERENCES agents(id),
    actor_id         uuid NOT NULL REFERENCES principals(id),
    model_id         uuid NOT NULL REFERENCES llm_models(id),
    title            text NOT NULL CHECK (char_length(title) BETWEEN 1 AND 240),
    prompt_snapshot  jsonb NOT NULL CHECK (jsonb_typeof(prompt_snapshot) = 'object'),
    context_snapshot jsonb NOT NULL CHECK (jsonb_typeof(context_snapshot) = 'object'),
    document_name    text NOT NULL CHECK (char_length(btrim(document_name)) BETWEEN 1 AND 240),
    folder           text NOT NULL CHECK (
        length(folder) <= 200 AND folder ~ '^[a-z0-9_-][a-z0-9._-]*(/[a-z0-9_-][a-z0-9._-]*)*$'
        AND folder !~ '(^|/)node_modules(/|$)' AND split_part(folder, '/', 1) <> 'services'),
    status           text NOT NULL DEFAULT 'starting'
        CHECK (status IN ('starting','working','waiting','stopped','failed','closed')),
    status_message   text NOT NULL DEFAULT '' CHECK (char_length(status_message) <= 2000),
    draft            text CHECK (char_length(draft) <= 1000000),
    draft_turn       integer CHECK (draft_turn > 0),
    document_id      uuid REFERENCES documents(id) ON DELETE SET NULL,
    claude_session_id text CHECK (char_length(claude_session_id) <= 200),
    turns            integer NOT NULL DEFAULT 0 CHECK (turns >= 0),
    input_tokens     bigint NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
    output_tokens    bigint NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
    cached_tokens    bigint NOT NULL DEFAULT 0 CHECK (cached_tokens >= 0),
    cost_usd         numeric(14,6) NOT NULL DEFAULT 0 CHECK (cost_usd >= 0),
    worker_id        text,
    lease_expires_at timestamptz,
    stop_requested_at timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    last_activity_at timestamptz NOT NULL DEFAULT now(),
    closed_at        timestamptz,
    CHECK ((status = 'closed') = (closed_at IS NOT NULL))
);
CREATE INDEX agent_sessions_cc_open ON agent_sessions_cc(created_at) WHERE status <> 'closed';
CREATE INDEX agent_sessions_cc_listing ON agent_sessions_cc(project_id, created_at DESC);
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON agent_sessions_cc FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

CREATE TABLE agent_session_inputs (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id   uuid NOT NULL REFERENCES agent_sessions_cc(id) ON DELETE CASCADE,
    seq          integer NOT NULL CHECK (seq > 0),
    actor_id     uuid NOT NULL REFERENCES principals(id),
    text         text NOT NULL CHECK (char_length(text) BETWEEN 1 AND 200000),
    status       text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','delivered','discarded')),
    turn         integer CHECK (turn > 0),
    created_at   timestamptz NOT NULL DEFAULT now(),
    delivered_at timestamptz,
    UNIQUE (session_id, seq),
    CHECK ((status = 'delivered') = (turn IS NOT NULL AND delivered_at IS NOT NULL))
);
CREATE INDEX agent_session_inputs_queued ON agent_session_inputs(session_id, seq) WHERE status = 'queued';
```

Create `db/migrations/0070_claude_code_agents.down.sql`:

```sql
SET search_path = ka, public;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM agents WHERE runtime = 'claude_code') THEN
        RAISE EXCEPTION 'Claude Code agents exist; delete their sessions and agents before rolling back 0070';
    END IF;
END $$;

DROP TABLE agent_session_inputs;
DROP TABLE agent_sessions_cc;
ALTER TABLE agents DROP CONSTRAINT agent_system_prompt_fk;
ALTER TABLE agents DROP COLUMN prompt_agent;
ALTER TABLE agents DROP CONSTRAINT claude_code_agent_is_a_registration;
ALTER TABLE agents DROP COLUMN runtime;
ALTER TABLE agents ADD CONSTRAINT agent_system_prompt_fk FOREIGN KEY (id) REFERENCES agent_prompts(agent)
    DEFERRABLE INITIALLY DEFERRED;
```

In `db/tests/run.sh`, change `[ "$rolled_back_version" = 68 ]` to `[ "$rolled_back_version" = 69 ]`.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_claude_code_migration.py tests/test_llm.py tests/test_profiles.py tests/test_agents.py -q`
Expected: PASS (the profile tests still see the OpenAI prompt rule: `test_system_prompt_is_required_by_api_and_database`).

Run: `bash db/tests/run.sh`
Expected: every `PASS:` line and exit code 0.

- [ ] **Step 5: Commit**

```bash
git add packages/llm-settings/src/knowledge_llm/__init__.py services/admin-api/tests/test_llm.py \
  db/migrations/0070_claude_code_agents.up.sql db/migrations/0070_claude_code_agents.down.sql \
  services/admin-api/tests/test_claude_code_migration.py db/tests/run.sh
git commit -m "feat(db): Claude Code agent runtime and interactive session tables (0070)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Claude Code agent registrations, hidden from OpenAI-only places

**Files:**
- Create: `services/admin-api/src/knowledge_admin/claude_code_api.py`
- Modify: `services/admin-api/src/knowledge_admin/api.py`
- Modify: `services/admin-api/src/knowledge_admin/agents.py` (`get_agent_config`, `CLAUDE_CODE_ONLY`)
- Modify: `services/admin-api/src/knowledge_admin/profiles_api.py` (update, model, delete)
- Modify: `services/admin-api/src/knowledge_admin/agent_versions.py` (`owner`)
- Modify: `services/admin-api/src/knowledge_admin/reader_settings.py`
- Modify: `services/admin-api/src/knowledge_admin/board.py` (`submit`)
- Modify: `packages/llm-settings/src/knowledge_llm/profiles.py`
- Create: `services/admin-api/tests/test_claude_code_agents.py`

**Interfaces:**
- Consumes: Task 1's `runtime` column.
- Produces: `POST /api/claude-code/agents {name, description}` → 201 `{id}`; `PUT /api/claude-code/agents/{id}
  {name, description, revision}` → `{id, name, description, runtime, enabled, revision}`; `runtime` in `GET
  /api/agents` and `GET /api/agents/{id}`; `knowledge_admin.agents.CLAUDE_CODE_ONLY`;
  `claude_code_api.build_api(app_name, store)` (Tasks 3–5 add to it).

- [ ] **Step 1: Write the failing tests**

Create `services/admin-api/tests/test_claude_code_agents.py`:

```python
"""Claude Code agents are registrations: a name and a description, kept out of every place that runs OpenAI agents."""
from uuid import uuid4

import pytest
from knowledge_llm import ModelUnavailable
from knowledge_llm.profiles import profile

from knowledge_admin.agents import get_agent_config
from test_api import _client
from test_repositories import HEADERS

FLAGS = ('documents_enabled', 'repositories_enabled', 'ssh_enabled', 'skills_enabled', 'mcp_enabled', 'services_enabled')


@pytest.fixture
def writer(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    response = client.post('/api/claude-code/agents', headers=HEADERS,
                           json={'name': 'Technical writer', 'description': 'Writes documentation'})
    assert response.status_code == 201, response.text
    return client, response.json()['id']


def test_claude_code_agent_is_created_listed_renamed_and_deleted(writer):
    client, agent_id = writer
    listed = next(a for a in client.get('/api/agents', headers=HEADERS).json()['agents'] if a['id'] == agent_id)
    assert (listed['runtime'], listed['model_id'], listed['prompt_version'], listed['config_version']) == ('claude_code', None, None, None)
    assert not any(listed[flag] for flag in FLAGS)
    detail = client.get(f'/api/agents/{agent_id}', headers=HEADERS).json()
    assert (detail['name'], detail['description'], detail['runtime'], detail['active_prompt']) == (
        'Technical writer', 'Writes documentation', 'claude_code', None)
    renamed = client.put(f'/api/claude-code/agents/{agent_id}', headers=HEADERS,
                         json={'name': 'Writer', 'description': '', 'revision': detail['revision']})
    assert renamed.status_code == 200, renamed.text
    assert (renamed.json()['name'], renamed.json()['revision']) == ('Writer', detail['revision'] + 1)
    assert client.put(f'/api/claude-code/agents/{agent_id}', headers=HEADERS,
                      json={'name': 'Again', 'revision': detail['revision']}).status_code == 409
    assert client.put('/api/claude-code/agents/search', headers=HEADERS, json={'name': 'Search', 'revision': 1}).status_code == 404
    assert client.post('/api/claude-code/agents', headers=HEADERS, json={'name': 'Writer'}).status_code == 409
    for bad in ({'name': ''}, {'name': 'a\x00b'}, {'name': 'Ok', 'model_id': str(uuid4())},
                {'name': 'Ok', 'instructions': 'Write well'}, {'name': 'Ok', 'revision': 1}):
        assert client.post('/api/claude-code/agents', headers=HEADERS, json=bad).status_code == 400, bad
    assert client.post('/api/claude-code/agents', json={'name': 'Anonymous'}).status_code == 403
    deleted = client.request('DELETE', f'/api/agents/{agent_id}', headers=HEADERS,
                             json={'revision': renamed.json()['revision'], 'confirmed': True})
    assert deleted.status_code == 204, deleted.text


def test_claude_code_agents_are_hidden_from_openai_only_places(store, writer):
    client, agent_id = writer
    with pytest.raises(ValueError, match='documentation sessions only'):
        get_agent_config(agent_id, conninfo=store.conninfo)
    assert client.post(f'/api/agents/{agent_id}/sessions', headers=HEADERS, json={'title': 'Test', 'is_test': True}).status_code == 409
    assert client.get(f'/api/agents/{agent_id}/versions/source', headers=HEADERS).status_code == 409
    detail = client.get(f'/api/agents/{agent_id}', headers=HEADERS).json()
    model = client.put(f'/api/agents/{agent_id}/model', headers=HEADERS, json={'revision': detail['revision'], 'model_id': None})
    assert model.status_code == 409 and 'when a session starts' in model.json()['error']['message']
    settings = {'revision': detail['revision'], 'name': 'Writer', 'description': '', 'when_to_use': '', 'enabled': True,
                'all_collections': False, 'all_repositories': False, 'collection_ids': [], 'repository_ids': [],
                'service_ids': [], **{flag: False for flag in FLAGS}}
    assert client.put(f'/api/agents/{agent_id}', headers=HEADERS, json=settings).status_code == 409
    candidates = client.get('/api/reader/settings', headers=HEADERS).json()['candidates']
    assert agent_id not in [candidate['id'] for candidate in candidates] and 'search' in [c['id'] for c in candidates]
    board = client.post('/api/agent-board/tasks', headers=HEADERS,
                        json={'id': str(uuid4()), 'task': 'Document payments', 'subagent_ids': [agent_id]})
    assert board.status_code == 400 and 'Claude Code agents' in board.json()['error']['message']
    with store.connect() as conn:
        assert profile(conn, agent_id)['runtime'] == 'claude_code'
        with pytest.raises(ModelUnavailable):
            profile(conn, agent_id, runnable=True)
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_claude_code_agents.py -q`
Expected: FAIL with 404 for `POST /api/claude-code/agents` (fixture assertion).

- [ ] **Step 3: Create `services/admin-api/src/knowledge_admin/claude_code_api.py`**

```python
"""Claude Code agents (registrations: a name and a description) and their interactive documentation sessions."""
from uuid import uuid4

import psycopg
from fastapi import APIRouter, HTTPException, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator

from knowledge_admin.access import request_actor
from knowledge_admin.repositories import RepositoryStore

AGENT_FIELDS = 'id,name,description,runtime,enabled,revision'


class AgentInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=4000)
    revision: int | None = Field(default=None, ge=1, strict=True)

    @field_validator('name', 'description')
    @classmethod
    def no_nul(cls, value: str) -> str:
        if '\x00' in value:
            raise ValueError('Text cannot contain null characters')
        return value


def build_api(app_name: str, store: RepositoryStore | None) -> APIRouter:
    router = APIRouter(prefix='/claude-code')

    def database() -> RepositoryStore:
        if store is None:
            raise HTTPException(503, 'Claude Code storage is not configured.')
        return store

    def audit(conn, actor, action: str, resource_type: str, details: dict, resource_id=None) -> None:
        conn.execute('''INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id,details)
            VALUES (%s,%s,%s,%s,%s,%s)''', (store.project(conn, app_name), actor, action, resource_type, resource_id, Jsonb(details)))

    @router.post('/agents', status_code=201)
    def create_agent(value: AgentInput, request: Request) -> dict:
        if value.revision is not None:
            raise HTTPException(400, 'New agents cannot reuse a revision')
        db = database()
        agent_id = str(uuid4())
        try:
            with db.connect() as conn:
                actor = db.actor(conn, *request_actor(request))
                conn.execute('''INSERT INTO ka.agents(id,name,description,runtime,created_by,updated_by)
                    VALUES (%s,%s,%s,'claude_code',%s,%s)''', (agent_id, value.name, value.description, actor, actor))
                audit(conn, actor, 'agent.created', 'agent', {'agent_id': agent_id, 'runtime': 'claude_code'})
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, 'An agent with this name already exists.') from None
        return {'id': agent_id}

    @router.put('/agents/{agent_id}')
    def update_agent(agent_id: str, value: AgentInput, request: Request) -> dict:
        db = database()
        try:
            with db.connect() as conn:
                row = conn.execute('SELECT revision,runtime FROM ka.agents WHERE id=%s FOR UPDATE', (agent_id,)).fetchone()
                if row is None or row['runtime'] != 'claude_code':
                    raise HTTPException(404, 'Claude Code agent not found.')
                if row['revision'] != value.revision:
                    raise HTTPException(409, 'Agent changed. Reload before saving.')
                actor = db.actor(conn, *request_actor(request))
                conn.execute('UPDATE ka.agents SET name=%s,description=%s,revision=revision+1,updated_by=%s WHERE id=%s',
                             (value.name, value.description, actor, agent_id))
                audit(conn, actor, 'agent.updated', 'agent', {'agent_id': agent_id, 'runtime': 'claude_code'})
                return conn.execute(f'SELECT {AGENT_FIELDS} FROM ka.agents WHERE id=%s', (agent_id,)).fetchone()
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, 'An agent with this name already exists.') from None

    return router
```

- [ ] **Step 4: Register it and refuse Claude Code agents in OpenAI-only places**

In `services/admin-api/src/knowledge_admin/api.py`, add `claude_code_api` to the `from knowledge_admin import …` line
and, after `router.include_router(agent_sessions.build_api(app_name, repository_store))`, add:

```python
    router.include_router(claude_code_api.build_api(app_name, repository_store))
```

In `services/admin-api/src/knowledge_admin/agents.py`, add below the imports:

```python
CLAUDE_CODE_ONLY = 'Claude Code agents run in documentation sessions only (Docs → Generate documents → With an agent).'
```

and in `get_agent_config`, add `runtime` to the agent `SELECT` column list (after `is_coordinator`) and, right after
the `is_coordinator` check, add:

```python
        if agent['runtime'] != 'openai':
            raise ValueError(CLAUDE_CODE_ONLY)
```

In `services/admin-api/src/knowledge_admin/profiles_api.py`:
- in `update`, right after `if old['revision'] != value.revision: …`, add:

```python
                if old['runtime'] != 'openai':
                    raise HTTPException(409, 'Claude Code agents have only a name and a description; edit them on their agent page.')
```

- in `model`, right after `old = read(conn, agent_id)`, add:

```python
            if old['runtime'] != 'openai':
                raise HTTPException(409, 'Claude Code agents choose their model when a session starts.')
```

- in `delete`, extend the retained-history query with `UNION ALL SELECT 1 FROM ka.agent_sessions_cc WHERE agent_id=%s`
  before `LIMIT 1` and pass `agent_id` a fifth time.

In `services/admin-api/src/knowledge_admin/agent_versions.py`, in `owner`, select `runtime` too
(`SELECT id,is_coordinator,runtime,active_config_version_id,next_config_version FROM ka.agents …`) and add after the
coordinator check:

```python
        if row['runtime'] != 'openai':
            raise HTTPException(409, 'Claude Code agents have no configuration versions.')
```

In `services/admin-api/src/knowledge_admin/reader_settings.py`, change the candidate query to
`SELECT id,name,enabled FROM ka.agents WHERE NOT is_coordinator AND runtime='openai' ORDER BY lower(name),id`.

In `services/admin-api/src/knowledge_admin/board.py`, in `submit`, add `a.runtime` to the `profiles` select list
(`SELECT a.id,a.name,a.enabled,a.is_coordinator,a.runtime,…`) and after the coordinator check add:

```python
            if any(p['runtime'] != 'openai' for p in profiles):
                raise HTTPException(400, 'Claude Code agents cannot join the multi-agent board.')
```

In `packages/llm-settings/src/knowledge_llm/profiles.py`, in `profile`, after the `row is None` check add:

```python
    if runnable and row.get('runtime', 'openai') != 'openai':
        raise ModelUnavailable('Agent not found or not supported by this runtime.')
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_claude_code_agents.py tests/test_profiles.py tests/test_agent_versions.py tests/test_reader_settings.py tests/test_board.py tests/test_agent_sessions.py tests/test_agents.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/admin-api/src/knowledge_admin/claude_code_api.py services/admin-api/src/knowledge_admin/api.py \
  services/admin-api/src/knowledge_admin/agents.py services/admin-api/src/knowledge_admin/profiles_api.py \
  services/admin-api/src/knowledge_admin/agent_versions.py services/admin-api/src/knowledge_admin/reader_settings.py \
  services/admin-api/src/knowledge_admin/board.py packages/llm-settings/src/knowledge_llm/profiles.py \
  services/admin-api/tests/test_claude_code_agents.py
git commit -m "feat(admin-api): Claude Code agent registrations kept out of OpenAI-only places

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---
### Task 3: Sessions — create with a context snapshot, list, detail, send, stop, close, idle close

**Files:**
- Create: `services/admin-api/src/knowledge_admin/claude_code.py`
- Modify: `services/admin-api/src/knowledge_admin/data_root.py` (add `DATA_FOLDER_RULE`, `data_folder`)
- Modify: `services/admin-api/src/knowledge_admin/claude_code_api.py`
- Modify: `services/admin-api/tests/test_data_root.py`
- Create: `services/admin-api/tests/test_claude_code_sessions.py`

**Interfaces:**
- Consumes: Task 1's tables and `claude_model`; Task 2's router; `documentation_folder`, `data_path`, `document_slug`,
  `PROMPT_CHANGED`.
- Produces, in `knowledge_admin.claude_code`: `SYSTEM_PROMPT`, `ALLOWED_TOOLS`, `DISALLOWED_TOOLS`, `FINAL_ANSWER`,
  `IDLE_CLOSED`, `first_message(prompt: str, comments: str, context: dict) -> str`, `close_idle(conn) -> int`.
- Produces, in `knowledge_admin.data_root`: `DATA_FOLDER_RULE`, `data_folder(value: str) -> str`.
- Produces, in `claude_code_api`: `SessionInput`, `MessageInput`, `session_context(conn, project, value) -> dict`,
  `session_detail(conn, project, session_id) -> dict`, and the routes `POST /api/claude-code/sessions` (201, detail),
  `GET /api/claude-code/sessions?offset=` (`{sessions: [summary]}`), `GET /api/claude-code/sessions/{id}` (detail with
  `inputs`), `POST …/messages {text}` (202 `{seq, status: 'queued', working}`), `POST …/stop` (202 `{id, discarded}`),
  `POST …/close` (`{id}`). Summary fields: `id, title, agent_id, agent_name, model_name, status, status_message,
  turns, input_tokens, output_tokens, cached_tokens, cost_usd, document_id, document_title, folder, worker_active,
  queued, created_at, last_activity_at, closed_at`; detail adds `document_name, draft, draft_turn, prompt_snapshot,
  context_snapshot, model_id, inputs: [{seq, text, status, turn, created_at}]`.

- [ ] **Step 1: Write the failing tests**

Append to `services/admin-api/tests/test_data_root.py` (and add `DATA_FOLDER_RULE, data_folder` to its
`knowledge_admin.data_root` import):

```python
def test_data_folders_follow_the_rag_rule():
    for good in ('jira', 'code/payments', 'Team/V1.2_x-y'):
        assert data_folder(good) == good
    for bad in ('', '/etc', '../etc', 'code/../etc', '.git', 'code/.hidden', 'a//b', 'my folder', 'x' * 501):
        with pytest.raises(ValueError) as error:
            data_folder(bad)
        assert str(error.value) == DATA_FOLDER_RULE
```

Create `services/admin-api/tests/test_claude_code_sessions.py`:

```python
"""Interactive Claude Code sessions: the context snapshot, queued messages, Stop, Close and idle closing."""
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from knowledge_admin.claude_code import FINAL_ANSWER, IDLE_CLOSED, SYSTEM_PROMPT
from knowledge_admin.documentation_generation import PROMPT_CHANGED
from test_api import _client
from test_repositories import HEADERS


@pytest.fixture
def sessions(store, tmp_path, monkeypatch):
    """A Claude Code agent, a Claude and an OpenAI model, a code project with one folder, a server and DATA_ROOT folders."""
    data = tmp_path / 'data'
    for folder in ('code/app', 'jira', 'generated-documents'):
        (data / folder).mkdir(parents=True)
    monkeypatch.setenv('DATA_ROOT_DIR', str(data))
    monkeypatch.setenv('CODE_ROOT', str(data / 'code'))
    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', str(data / 'generated-documents'))
    client = _client(tmp_path, repository_store=store)
    agent = client.post('/api/claude-code/agents', headers=HEADERS, json={'name': 'Technical writer'}).json()['id']
    claude = client.post('/api/llm/models', headers=HEADERS, json={
        'name': 'Claude', 'provider': 'bedrock', 'model': 'eu.anthropic.claude-sonnet-4-5-20250929-v1:0',
        'api_format': 'native', 'aws_region': 'eu-central-1', 'api_key': 'bedrock-secret-token'}).json()['id']
    gpt = client.post('/api/llm/models', headers=HEADERS, json={
        'name': 'GPT', 'provider': 'openai', 'model': 'gpt-5', 'api_format': 'responses',
        'base_url': 'http://model.test/v1'}).json()['id']
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        repo = uuid4()
        conn.execute("INSERT INTO ka.repositories(id,project_id,slug,name) VALUES (%s,%s,'payments-api','Payments API')", (repo, project))
        conn.execute("INSERT INTO ka.code_project_folders(code_project_id,project_id,path) VALUES (%s,%s,'app')", (repo, project))
    server = client.post('/api/servers', headers=HEADERS, json={
        'name': 'prod-1', 'hostname': '10.0.0.5', 'description': 'Payments host'}).json()
    prompt = next(p for p in client.get('/api/documentation/prompts', headers=HEADERS).json()['prompts']
                  if p['name'] == 'Code analysis and architecture')
    body = {'agent_id': agent, 'model_id': claude, 'prompt_id': prompt['id'], 'prompt_version_id': prompt['active_version_id'],
            'comments': 'Focus on the public API.', 'code_project_ids': [str(repo)], 'folders': ['jira'],
            'server_ids': [server['id']], 'notes': 'Deployed with Helm.',
            'document_name': 'Payments – API reference', 'folder': 'payments-api-docs'}
    return SimpleNamespace(client=client, store=store, body=body, agent=agent, claude=claude, gpt=gpt, repo=str(repo),
                           server=server, data=data, prompt=prompt)


def start(sessions, **change) -> dict:
    response = sessions.client.post('/api/claude-code/sessions', headers=HEADERS, json={**sessions.body, **change})
    assert response.status_code == 201, response.text
    return response.json()


def test_session_context_snapshot_folders_servers_and_notes(sessions):
    session = start(sessions)
    data = sessions.data
    assert (session['status'], session['turns'], session['title'], session['folder'], session['agent_name'], session['model_name']) == (
        'starting', 0, 'Payments – API reference', 'payments-api-docs', 'Technical writer', 'Claude')
    assert session['context_snapshot'] == {
        'folders': [{'path': f'{data}/code/app', 'kind': 'code_project', 'name': 'Payments API', 'code_project_id': sessions.repo},
                    {'path': f'{data}/jira', 'kind': 'folder', 'name': 'jira'}],
        'code_project_ids': [sessions.repo],
        'servers': [{'id': sessions.server['id'], 'name': 'prod-1', 'hostname': '10.0.0.5', 'description': 'Payments host'}],
        'notes': 'Deployed with Helm.'}
    snapshot = session['prompt_snapshot']
    assert (snapshot['system_prompt'], snapshot['version_id'], snapshot['comments']) == (
        SYSTEM_PROMPT, sessions.prompt['active_version_id'], 'Focus on the public API.')
    [first] = session['inputs']
    assert (first['seq'], first['status'], first['turn']) == (1, 'queued', None)
    assert first['text'].startswith(snapshot['content'].strip())
    for part in ('## Comments for this run\n\nFocus on the public API.', '## Context',
                 f'- {data}/code/app (code project Payments API)', f'- {data}/jira\n', '- prod-1 (10.0.0.5): Payments host',
                 'Notes:\n\nDeployed with Helm.', FINAL_ANSWER):
        assert part in first['text'], part
    assert 'bedrock-secret-token' not in json.dumps(session)
    # A DATA_ROOT folder alone is enough context; without servers, notes and comments those sections are left out.
    folder_only = start(sessions, code_project_ids=[], server_ids=[], notes='', comments='')
    text = folder_only['inputs'][0]['text']
    assert folder_only['context_snapshot']['folders'] == [{'path': f'{data}/jira', 'kind': 'folder', 'name': 'jira'}]
    assert 'Servers' not in text and 'Notes:' not in text and 'Comments for this run' not in text


@pytest.mark.parametrize('change, status', [
    ({'model_id': 'gpt'}, 400), ({'agent_id': 'search'}, 400), ({'agent_id': 'missing'}, 400),
    ({'folders': ['../etc']}, 400), ({'folders': ['/etc']}, 400), ({'folders': ['.git']}, 400), ({'folders': ['missing']}, 400),
    ({'code_project_ids': [], 'folders': []}, 400), ({'code_project_ids': [str(uuid4())]}, 400),
    ({'folder': 'Payments'}, 400), ({'folder': 'services/x'}, 400), ({'folder': '../docs'}, 400),
    ({'document_name': '– –'}, 400), ({'notes': 'a\x00b'}, 400), ({'comments': 'x' * 20001}, 400),
    ({'server_ids': [str(uuid4())]}, 400), ({'prompt_version_id': str(uuid4())}, 409), ({'prompt_id': str(uuid4())}, 400)])
def test_session_input_is_validated(sessions, change, status):
    if change.get('model_id') == 'gpt':
        change = {'model_id': sessions.gpt}
    response = sessions.client.post('/api/claude-code/sessions', headers=HEADERS, json={**sessions.body, **change})
    assert response.status_code == status, response.text
    if status == 409:
        assert response.json()['error']['message'] == PROMPT_CHANGED
    with sessions.store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_sessions_cc').fetchone()['n'] == 0


def test_a_removed_folder_or_disabled_agent_refuses_the_session(sessions):
    (sessions.data / 'code/app').rmdir()
    response = sessions.client.post('/api/claude-code/sessions', headers=HEADERS, json=sessions.body)
    assert response.status_code == 400 and 'does not exist' in response.json()['error']['message']
    with sessions.store.connect() as conn:
        conn.execute('UPDATE ka.agents SET enabled=false WHERE id=%s', (sessions.agent,))
    response = sessions.client.post('/api/claude-code/sessions', headers=HEADERS, json={**sessions.body, 'code_project_ids': []})
    assert response.status_code == 400 and response.json()['error']['message'] == 'Select an enabled Claude Code agent.'


def test_messages_stop_and_close(sessions):
    client, store = sessions.client, sessions.store
    session = start(sessions)
    url = f"/api/claude-code/sessions/{session['id']}"
    assert client.post(url + '/messages', headers=HEADERS, json={'text': ' Also cover retries. '}).json() == {
        'seq': 2, 'status': 'queued', 'working': False}
    assert client.post(url + '/messages', headers=HEADERS, json={'text': '   '}).status_code == 400
    assert client.post(url + '/stop', headers=HEADERS).status_code == 409  # starting: nothing runs yet
    with store.connect() as conn:  # a worker delivered both messages as turn 1
        conn.execute("UPDATE ka.agent_sessions_cc SET status='working',turns=1 WHERE id=%s", (session['id'],))
        conn.execute("UPDATE ka.agent_session_inputs SET status='delivered',turn=1,delivered_at=now() WHERE session_id=%s", (session['id'],))
    assert client.post(url + '/messages', headers=HEADERS, json={'text': 'Shorten it.'}).json()['working'] is True
    stopped = client.post(url + '/stop', headers=HEADERS)
    assert stopped.status_code == 202 and stopped.json()['discarded'] == 1
    detail = client.get(url, headers=HEADERS).json()
    assert [(i['seq'], i['text'], i['status'], i['turn']) for i in detail['inputs'][1:]] == [
        (2, 'Also cover retries.', 'delivered', 1), (3, 'Shorten it.', 'discarded', None)]
    with store.connect() as conn:
        assert conn.execute('SELECT stop_requested_at FROM ka.agent_sessions_cc WHERE id=%s', (session['id'],)).fetchone()['stop_requested_at']
    assert client.post(url + '/close', headers=HEADERS).status_code == 200
    detail = client.get(url, headers=HEADERS).json()
    assert (detail['status'], detail['status_message'], bool(detail['closed_at'])) == ('closed', 'Closed.', True)
    closed = client.post(url + '/messages', headers=HEADERS, json={'text': 'Hello?'})
    assert closed.status_code == 409 and closed.json()['error']['message'] == 'This session is closed. Start a new session.'
    assert client.post(url + '/close', headers=HEADERS).status_code == 200  # idempotent
    listed = client.get('/api/claude-code/sessions', headers=HEADERS).json()['sessions']
    assert [(s['id'], s['status'], s['agent_name'], s['model_name'], s['queued']) for s in listed] == [
        (session['id'], 'closed', 'Technical writer', 'Claude', 0)]
    assert 'draft' not in listed[0] and 'prompt_snapshot' not in listed[0]
    assert client.get(f'/api/claude-code/sessions/{uuid4()}', headers=HEADERS).status_code == 404
    for path in (url + '/messages', url + '/stop', url + '/close'):
        assert client.post(path, json={'text': 'x'}).status_code == 403
    assert client.get('/api/claude-code/sessions').status_code == 403
    revision = client.get(f'/api/agents/{sessions.agent}', headers=HEADERS).json()['revision']
    assert client.request('DELETE', f'/api/agents/{sessions.agent}', headers=HEADERS,
                          json={'revision': revision, 'confirmed': True}).status_code == 409  # retained session history


def test_idle_sessions_close_after_24_hours(sessions):
    ids = [start(sessions)['id'] for _ in range(3)]
    with sessions.store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions_cc SET last_activity_at=now()-interval '25 hours' WHERE id=ANY(%s::uuid[])", (ids[:2],))
        conn.execute("UPDATE ka.agent_sessions_cc SET status='working' WHERE id=%s", (ids[1],))  # a running turn is never idle
    listed = {s['id']: s for s in sessions.client.get('/api/claude-code/sessions', headers=HEADERS).json()['sessions']}
    assert (listed[ids[0]]['status'], listed[ids[0]]['status_message']) == ('closed', IDLE_CLOSED)
    assert (listed[ids[1]]['status'], listed[ids[2]]['status']) == ('working', 'starting')
    inputs = sessions.client.get(f'/api/claude-code/sessions/{ids[0]}', headers=HEADERS).json()['inputs']
    assert [i['status'] for i in inputs] == ['discarded']
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_claude_code_sessions.py tests/test_data_root.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'knowledge_admin.claude_code'` and
`ImportError: cannot import name 'DATA_FOLDER_RULE'`.

- [ ] **Step 3: Add the DATA_ROOT folder rule**

Append to `services/admin-api/src/knowledge_admin/data_root.py`:

```python
DATA_SEGMENT = r'[A-Za-z0-9_-][A-Za-z0-9._-]*'
DATA_FOLDER = re.compile(rf'{DATA_SEGMENT}(/{DATA_SEGMENT})*')
DATA_FOLDER_RULE = ("A folder is a path under DATA_ROOT such as code/payments or jira: names of letters, digits, '.', '_' "
                    "and '-' separated by '/', none starting with '.'; at most 500 characters.")


def data_folder(value: str) -> str:
    """A folder under DATA_ROOT as RAG names it (relative, no '..', no hidden segment). Raises ValueError(DATA_FOLDER_RULE)."""
    if len(value) > 500 or not DATA_FOLDER.fullmatch(value):
        raise ValueError(DATA_FOLDER_RULE)
    return value
```

- [ ] **Step 4: Create `services/admin-api/src/knowledge_admin/claude_code.py`**

```python
"""Claude Code sessions: fixed tools, the built-in system prompt, the first message and idle closing (admin-api and worker)."""
from typing import Any

SYSTEM_PROMPT = ('You are an expert technical writer. You explore the provided folders and context to write accurate '
                 'documentation; everything you state must be grounded in what you read.')
ALLOWED_TOOLS = ('Read', 'Grep', 'Glob', 'Bash')
DISALLOWED_TOOLS = ('Edit', 'Write', 'NotebookEdit', 'Agent', 'Task', 'Skill', 'WebFetch', 'WebSearch', 'TodoWrite')
FINAL_ANSWER = ('When you finish, reply with the complete document in Markdown as your final answer: it becomes the draft. '
                'After each later message, reply again with the complete updated document.')
IDLE_CLOSED = 'Closed after 24 hours without activity.'


def first_message(prompt: str, comments: str, context: dict[str, Any]) -> str:
    """The one message that starts a session: the prompt, this run's comments and the context section."""
    parts = [prompt.strip()]
    if comments.strip():
        parts.append('## Comments for this run\n\n' + comments.strip())
    lines = ['## Context', '', 'Working folders (read-only; the first one is your working directory):']
    lines += [f"- {f['path']}" + (f" (code project {f['name']})" if f['kind'] == 'code_project' else '') for f in context['folders']]
    if context['servers']:
        lines += ['', 'Servers (for reference only; you have no access to them):']
        lines += [f"- {s['name']} ({s['hostname']})" + (f": {s['description']}" if s['description'] else '') for s in context['servers']]
    if context['notes'].strip():
        lines += ['', 'Notes:', '', context['notes'].strip()]
    parts.append('\n'.join(lines))
    parts.append(FINAL_ANSWER)
    return '\n\n'.join(parts)


def close_idle(conn) -> int:
    """Close sessions without activity for 24 hours (never one with a running turn); their history is kept."""
    rows = conn.execute("""UPDATE ka.agent_sessions_cc SET status='closed',closed_at=now(),status_message=%s
        WHERE status IN ('starting','waiting','stopped','failed') AND last_activity_at < now() - interval '24 hours'
        RETURNING id""", (IDLE_CLOSED,)).fetchall()
    if rows:
        conn.execute("UPDATE ka.agent_session_inputs SET status='discarded' WHERE status='queued' AND session_id=ANY(%s)",
                     ([row['id'] for row in rows],))
    return len(rows)
```

- [ ] **Step 5: Add the session routes to `claude_code_api.py`**

Replace the import block with:

```python
import os
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4

import psycopg
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from knowledge_llm import claude_model
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from knowledge_admin.access import request_actor
from knowledge_admin.claude_code import SYSTEM_PROMPT, close_idle, first_message
from knowledge_admin.data_root import data_folder, data_path, documentation_folder
from knowledge_admin.documentation_generation import PROMPT_CHANGED
from knowledge_admin.documents import document_slug
from knowledge_admin.repositories import RepositoryStore
```

Add below `AgentInput`:

```python
NOT_CLAUDE = 'Select an enabled Claude model (Bedrock or Anthropic) from LLM Settings.'
CLOSED = 'This session is closed. Start a new session.'
NOTHING_TO_STOP = 'The agent is not working, so there is nothing to stop.'


class SessionInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    agent_id: str = Field(min_length=1, max_length=100)
    model_id: UUID
    prompt_id: UUID
    prompt_version_id: UUID
    comments: str = Field(default='', max_length=20000)
    code_project_ids: list[UUID] = Field(default_factory=list, max_length=20)
    folders: list[str] = Field(default_factory=list, max_length=20)
    server_ids: list[UUID] = Field(default_factory=list, max_length=50)
    notes: str = Field(default='', max_length=20000)
    document_name: str = Field(max_length=240)
    folder: str = Field(max_length=200)

    @model_validator(mode='after')
    def valid(self):
        if any('\x00' in text for text in (self.comments, self.notes, self.document_name)):
            raise ValueError('Text cannot contain null characters')
        self.document_name = self.document_name.strip()
        if not document_slug(self.document_name):
            raise ValueError('Use letters or digits in the document name')
        self.folder = documentation_folder(self.folder)
        self.folders = list(dict.fromkeys(data_folder(folder) for folder in self.folders))
        self.code_project_ids = list(dict.fromkeys(self.code_project_ids))
        self.server_ids = list(dict.fromkeys(self.server_ids))
        if not self.code_project_ids and not self.folders:
            raise ValueError('Add at least one code project or folder as context')
        return self


class MessageInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=1, max_length=20000)

    @field_validator('text')
    @classmethod
    def valid_text(cls, value: str) -> str:
        if '\x00' in value or not value.strip():
            raise ValueError('Enter a message without null characters')
        return value.strip()


def _select(extra: str = '') -> str:
    return f"""SELECT s.id,s.title,s.agent_id,a.name AS agent_name,m.name AS model_name,s.status,s.status_message,s.turns,
        s.input_tokens,s.output_tokens,s.cached_tokens,s.cost_usd,s.document_id,d.title AS document_title,s.folder,
        coalesce(s.lease_expires_at > now(), false) AS worker_active,
        (SELECT count(*) FROM ka.agent_session_inputs i WHERE i.session_id=s.id AND i.status='queued') AS queued,
        s.created_at,s.last_activity_at,s.closed_at{extra}
        FROM ka.agent_sessions_cc s JOIN ka.agents a ON a.id=s.agent_id JOIN ka.llm_models m ON m.id=s.model_id
        LEFT JOIN ka.document_details d ON d.id=s.document_id"""


SUMMARY = _select()
DETAIL = _select(',s.document_name,s.draft,s.draft_turn,s.prompt_snapshot,s.context_snapshot,s.model_id')


def session_detail(conn, project: UUID, session_id: UUID) -> dict:
    row = conn.execute(DETAIL + ' WHERE s.id=%s AND s.project_id=%s', (session_id, project)).fetchone()
    if row is None:
        raise HTTPException(404, 'Session not found.')
    row['inputs'] = conn.execute('''SELECT seq,text,status,turn,created_at FROM ka.agent_session_inputs
        WHERE session_id=%s ORDER BY seq''', (session_id,)).fetchall()
    return row


def session_context(conn, project: UUID, value: SessionInput) -> dict:
    """Working folders as container paths (code project folders in the chosen order, then DATA_ROOT folders), servers
    as text and the notes. Every folder must exist now."""
    code_root = PurePosixPath(os.environ.get('CODE_ROOT', ''))
    if value.code_project_ids and (not code_root.is_absolute() or '..' in code_root.parts):
        raise HTTPException(503, 'CODE_ROOT must be an absolute mounted path')
    projects = {row['id']: row for row in conn.execute("""SELECT r.id,r.name,
        ARRAY(SELECT path::text FROM ka.code_project_folders f WHERE f.code_project_id=r.id ORDER BY path) AS folder_paths
        FROM ka.repositories r WHERE r.project_id=%s AND r.id=ANY(%s) AND r.archived_at IS NULL""",
        (project, value.code_project_ids)).fetchall()}
    if len(projects) != len(value.code_project_ids):
        raise HTTPException(400, 'Select available code projects.')
    candidates = []
    for project_id in value.code_project_ids:
        item = projects[project_id]
        if not item['folder_paths']:
            raise HTTPException(400, f"The code project {item['name']} has no folders.")
        candidates += [{'path': str(code_root / path), 'kind': 'code_project', 'name': item['name'],
                        'code_project_id': str(project_id)} for path in item['folder_paths']]
    candidates += [{'path': str(data_path(folder)), 'kind': 'folder', 'name': folder} for folder in value.folders]
    folders, seen = [], set()
    for item in candidates:
        if item['path'] in seen:
            continue
        seen.add(item['path'])
        if not Path(item['path']).is_dir():
            raise HTTPException(400, f"The folder {item['path']} does not exist. Remove it from the context.")
        folders.append(item)
    servers = conn.execute('''SELECT id,name,hostname,description FROM ka.servers WHERE project_id=%s AND id=ANY(%s)
        ORDER BY lower(name),id''', (project, value.server_ids)).fetchall()
    if len(servers) != len(value.server_ids):
        raise HTTPException(400, 'Select available servers.')
    return jsonable_encoder({'folders': folders, 'code_project_ids': value.code_project_ids, 'servers': servers,
                             'notes': value.notes})
```

Inside `build_api`, before `return router`, add:

```python
    def locked(conn, project, session_id: UUID) -> dict:
        row = conn.execute('SELECT id,status FROM ka.agent_sessions_cc WHERE id=%s AND project_id=%s FOR UPDATE',
                           (session_id, project)).fetchone()
        if row is None:
            raise HTTPException(404, 'Session not found.')
        return row

    def discard(conn, session_id: UUID) -> int:
        return conn.execute("UPDATE ka.agent_session_inputs SET status='discarded' WHERE session_id=%s AND status='queued'",
                            (session_id,)).rowcount

    @router.post('/sessions', status_code=201)
    def create_session(value: SessionInput, request: Request) -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app_name)
            actor = db.actor(conn, *request_actor(request))
            agent = conn.execute('SELECT id,enabled,runtime FROM ka.agents WHERE id=%s FOR SHARE', (value.agent_id,)).fetchone()
            if agent is None or agent['runtime'] != 'claude_code' or not agent['enabled']:
                raise HTTPException(400, 'Select an enabled Claude Code agent.')
            model = conn.execute('SELECT id,provider,model,api_format,enabled FROM ka.llm_models WHERE id=%s FOR SHARE',
                                 (value.model_id,)).fetchone()
            if model is None or not model['enabled'] or not claude_model(model['provider'], model['api_format'], model['model']):
                raise HTTPException(400, NOT_CLAUDE)
            prompt = conn.execute("""SELECT p.id,p.name,v.id AS version_id,v.version,v.content
                FROM ka.documentation_prompts p JOIN ka.documentation_prompt_versions v ON v.id=p.active_version_id
                WHERE p.id=%s AND p.project_id=%s AND p.enabled""", (value.prompt_id, project)).fetchone()
            if prompt is None:
                raise HTTPException(400, 'Select an active documentation prompt.')
            if prompt['version_id'] != value.prompt_version_id:
                raise HTTPException(409, PROMPT_CHANGED)
            context = session_context(conn, project, value)
            snapshot = jsonable_encoder({**prompt, 'comments': value.comments, 'system_prompt': SYSTEM_PROMPT})
            session_id = uuid4()
            conn.execute("""INSERT INTO ka.agent_sessions_cc(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,
                context_snapshot,document_name,folder) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (session_id, project, agent['id'], actor, model['id'], value.document_name, Jsonb(snapshot), Jsonb(context),
                 value.document_name, value.folder))
            conn.execute('INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text) VALUES (%s,1,%s,%s)',
                         (session_id, actor, first_message(prompt['content'], value.comments, context)))
            audit(conn, actor, 'claude_code.session.created', 'agent_session', {'agent_id': agent['id']}, session_id)
            return session_detail(conn, project, session_id)

    @router.get('/sessions')
    def sessions(response: Response, offset: int = Query(0, ge=0)) -> dict:
        response.headers['Cache-Control'] = 'no-store'
        db = database()
        with db.connect() as conn:
            close_idle(conn)
            project = db.project(conn, app_name)
            return {'sessions': conn.execute(SUMMARY + ' WHERE s.project_id=%s ORDER BY s.created_at DESC,s.id LIMIT 50 OFFSET %s',
                                             (project, offset)).fetchall()}

    @router.get('/sessions/{session_id}')
    def session(session_id: UUID, response: Response) -> dict:
        response.headers['Cache-Control'] = 'no-store'
        db = database()
        with db.connect() as conn:
            return session_detail(conn, db.project(conn, app_name), session_id)

    @router.post('/sessions/{session_id}/messages', status_code=202)
    def send(session_id: UUID, value: MessageInput, request: Request) -> dict:
        db = database()
        with db.connect() as conn:
            row = locked(conn, db.project(conn, app_name), session_id)
            if row['status'] == 'closed':
                raise HTTPException(409, CLOSED)
            actor = db.actor(conn, *request_actor(request))
            seq = conn.execute('SELECT coalesce(max(seq),0)+1 AS seq FROM ka.agent_session_inputs WHERE session_id=%s',
                               (session_id,)).fetchone()['seq']
            conn.execute('INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text) VALUES (%s,%s,%s,%s)',
                         (session_id, seq, actor, value.text))
            conn.execute('UPDATE ka.agent_sessions_cc SET last_activity_at=now() WHERE id=%s', (session_id,))
            return {'seq': seq, 'status': 'queued', 'working': row['status'] == 'working'}

    @router.post('/sessions/{session_id}/stop', status_code=202)
    def stop(session_id: UUID, request: Request) -> dict:
        db = database()
        with db.connect() as conn:
            row = locked(conn, db.project(conn, app_name), session_id)
            if row['status'] != 'working':
                raise HTTPException(409, NOTHING_TO_STOP)
            actor = db.actor(conn, *request_actor(request))
            conn.execute('UPDATE ka.agent_sessions_cc SET stop_requested_at=now() WHERE id=%s', (session_id,))
            discarded = discard(conn, session_id)
            audit(conn, actor, 'claude_code.session.stopped', 'agent_session', {'discarded': discarded}, session_id)
            return {'id': session_id, 'discarded': discarded}

    @router.post('/sessions/{session_id}/close')
    def close(session_id: UUID, request: Request) -> dict:
        db = database()
        with db.connect() as conn:
            row = locked(conn, db.project(conn, app_name), session_id)
            actor = db.actor(conn, *request_actor(request))
            if row['status'] != 'closed':
                conn.execute("""UPDATE ka.agent_sessions_cc SET status='closed',closed_at=now(),status_message='Closed.'
                    WHERE id=%s""", (session_id,))
                discard(conn, session_id)
                audit(conn, actor, 'claude_code.session.closed', 'agent_session', {}, session_id)
            return {'id': session_id}
```

- [ ] **Step 6: Run the tests and confirm they pass**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_claude_code_sessions.py tests/test_data_root.py tests/test_claude_code_agents.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add services/admin-api/src/knowledge_admin/claude_code.py services/admin-api/src/knowledge_admin/data_root.py \
  services/admin-api/src/knowledge_admin/claude_code_api.py services/admin-api/tests/test_data_root.py \
  services/admin-api/tests/test_claude_code_sessions.py
git commit -m "feat(admin-api): Claude Code sessions with a context snapshot, queued messages, stop and close

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: The session event stream (SSE)

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/claude_code_api.py`
- Modify: `services/admin-api/tests/test_claude_code_sessions.py`

**Interfaces:**
- Consumes: Task 3's session tables and routes; `ka.agent_run_events` written by the worker (Task 7).
- Produces: `GET /api/claude-code/sessions/{id}/events?after=<event id>` (honours `Last-Event-ID`),
  `STREAM_POLL_SECONDS`, `stream_event(row) -> dict`, `STATE` (the session state query).

- [ ] **Step 1: Write the failing test**

Add `import threading`, `import time` and `from psycopg.types.json import Jsonb` to
`services/admin-api/tests/test_claude_code_sessions.py`, add `from knowledge_admin import claude_code_api`, and append:

```python
def parse(text: str) -> list[dict]:
    events = []
    for block in text.strip().split('\n\n'):
        fields = dict(line.split(': ', 1) for line in block.split('\n') if line and not line.startswith(':'))
        if 'event' in fields:
            events.append({'event': fields['event'], 'id': int(fields['id']) if 'id' in fields else None,
                           'data': json.loads(fields['data'])})
    return events


def record(conn, session_id, turn: int, seq: int, kind: str, payload: dict) -> None:
    conn.execute('''INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,payload)
        SELECT project_id,%s,%s,%s,agent_id,%s FROM ka.agent_sessions_cc WHERE id=%s''',
        (f'cc:{session_id}:{turn}', seq, kind, Jsonb(payload), session_id))


def test_event_stream_replays_history_and_follows_a_fake_worker(sessions, monkeypatch):
    monkeypatch.setattr(claude_code_api, 'STREAM_POLL_SECONDS', 0.02)
    client, store = sessions.client, sessions.store
    sid = start(sessions)['id']
    other = start(sessions)['id']
    with store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions_cc SET status='working',turns=1 WHERE id=ANY(%s::uuid[])", ([sid, other],))
        record(conn, sid, 1, 1, 'start', {'kind': 'claude_code', 'title': 'Payments'})
        record(conn, other, 1, 1, 'start', {'kind': 'claude_code', 'title': 'Another session'})
        record(conn, sid, 1, 2, 'tool_call', {'name': 'Read', 'summary': '/work/data/code/app/main.py'})
        record(conn, sid, 1, 3, 'tool_result', {'name': 'Read', 'output': 'line\n' * 500, 'preview': 'line\nline'})

    def worker():  # a fake worker finishes the turn, then the session is closed
        time.sleep(0.3)
        with store.connect() as conn:
            record(conn, sid, 1, 4, 'message', {'text': '# Payments API'})
            conn.execute("UPDATE ka.agent_sessions_cc SET status='waiting',draft='# Payments API',draft_turn=1 WHERE id=%s", (sid,))
        time.sleep(0.3)
        with store.connect() as conn:
            conn.execute("UPDATE ka.agent_sessions_cc SET status='closed',closed_at=now() WHERE id=%s", (sid,))

    thread = threading.Thread(target=worker)
    thread.start()
    response = client.get(f'/api/claude-code/sessions/{sid}/events', headers=HEADERS)
    thread.join()
    assert response.status_code == 200 and response.headers['content-type'].startswith('text/event-stream')
    assert response.headers['cache-control'] == 'no-store'
    events = parse(response.text)
    runs = [e for e in events if e['event'] == 'run_event']
    assert [(e['data']['turn'], e['data']['seq'], e['data']['kind']) for e in runs] == [
        (1, 1, 'start'), (1, 2, 'tool_call'), (1, 3, 'tool_result'), (1, 4, 'message')]
    assert all(e['id'] == e['data']['id'] for e in runs)
    assert runs[2]['data']['payload'] == {'name': 'Read', 'preview': 'line\nline'}  # the full output stays in the run record
    states = [e['data'] for e in events if e['event'] == 'session']
    assert states[0]['status'] == 'working' and states[-1]['status'] == 'closed'
    assert any(s['status'] == 'waiting' and s['draft'] == '# Payments API' for s in states)
    assert events[-1]['event'] == 'closed'
    resumed = client.get(f'/api/claude-code/sessions/{sid}/events', headers={**HEADERS, 'Last-Event-ID': str(runs[1]['id'])})
    assert [e['data']['kind'] for e in parse(resumed.text) if e['event'] == 'run_event'] == ['tool_result', 'message']
    after = client.get(f"/api/claude-code/sessions/{sid}/events?after={runs[2]['id']}", headers=HEADERS)
    assert [e['data']['kind'] for e in parse(after.text) if e['event'] == 'run_event'] == ['message']
    assert client.get(f'/api/claude-code/sessions/{uuid4()}/events', headers=HEADERS).status_code == 404
    assert client.get(f'/api/claude-code/sessions/{sid}/events').status_code == 403
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_claude_code_sessions.py::test_event_stream_replays_history_and_follows_a_fake_worker -q`
Expected: FAIL with `AttributeError: … has no attribute 'STREAM_POLL_SECONDS'`.

- [ ] **Step 3: Implement the stream**

In `claude_code_api.py`, add `import asyncio` and `import json` to the imports, plus
`from fastapi.responses import StreamingResponse` and `from psycopg.rows import dict_row`. Add at module level:

```python
STREAM_POLL_SECONDS = 1.0
STATE = """SELECT s.status,s.status_message,s.turns,s.input_tokens,s.output_tokens,s.cached_tokens,s.cost_usd,s.draft,
    s.draft_turn,s.document_id,coalesce(s.lease_expires_at > now(), false) AS worker_active,
    (SELECT count(*) FROM ka.agent_session_inputs i WHERE i.session_id=s.id AND i.status='queued') AS queued
    FROM ka.agent_sessions_cc s WHERE s.id=%s"""


def stream_event(row: dict) -> dict:
    """One run event for the page; a tool result keeps its preview, the full output stays in the run record."""
    payload = dict(row['payload'])
    if row['kind'] == 'tool_result':
        payload.pop('output', None)
    return {'id': row['id'], 'turn': int(row['run_key'].rsplit(':', 1)[1]), 'seq': row['seq'], 'kind': row['kind'],
            'payload': payload, 'created_at': row['created_at']}
```

Inside `build_api`, add:

```python
    @router.get('/sessions/{session_id}/events')
    async def events(session_id: UUID, request: Request, after: int = Query(0, ge=0)):
        db = database()
        with db.connect() as conn:
            if conn.execute('SELECT 1 FROM ka.agent_sessions_cc WHERE id=%s AND project_id=%s',
                            (session_id, db.project(conn, app_name))).fetchone() is None:
                raise HTTPException(404, 'Session not found.')
        last = request.headers.get('last-event-id', '')
        start_after = int(last) if last.isdigit() else after

        async def stream():
            # Poll by event id over this session's turn keys (cc:<session>:<turn>) and the session row; leaving the
            # page closes only this subscriber.
            position, state = start_after, None
            try:
                async with await psycopg.AsyncConnection.connect(db.conninfo, autocommit=True, row_factory=dict_row) as conn:
                    while True:
                        current = await (await conn.execute(STATE, (session_id,))).fetchone()
                        keys = [f'cc:{session_id}:{turn}' for turn in range(1, current['turns'] + 1)]
                        rows = await (await conn.execute('''SELECT id,run_key,seq,kind,payload,created_at
                            FROM ka.agent_run_events WHERE run_key=ANY(%s) AND id>%s ORDER BY id LIMIT 500''',
                            (keys, position))).fetchall()
                        for row in rows:
                            position = row['id']
                            yield f'id: {position}\nevent: run_event\ndata: {json.dumps(jsonable_encoder(stream_event(row)))}\n\n'
                        if len(rows) == 500:
                            continue
                        public = jsonable_encoder(current)
                        if public != state:
                            state = public
                            yield f'event: session\ndata: {json.dumps(public)}\n\n'
                        if current['status'] == 'closed':
                            yield 'event: closed\ndata: {}\n\n'
                            return
                        if await request.is_disconnected():
                            return
                        yield ': keepalive\n\n'
                        await asyncio.sleep(STREAM_POLL_SECONDS)
            except psycopg.Error:
                yield 'event: unavailable\ndata: {}\n\n'

        return StreamingResponse(stream(), media_type='text/event-stream',
                                 headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_claude_code_sessions.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/claude_code_api.py services/admin-api/tests/test_claude_code_sessions.py
git commit -m "feat(admin-api): stream Claude Code session events over SSE

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Save the draft as a document

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/claude_code_api.py`
- Modify: `services/admin-api/tests/test_claude_code_sessions.py`

**Interfaces:**
- Consumes: `save_document`, `DocumentInput`, `export_document`, `export_args`, `export_input`, `document_slug`
  (`knowledge_admin.documents`); `SAME_DOCUMENT_NAME` (`knowledge_admin.documentation_generation`).
- Produces: `POST /api/claude-code/sessions/{id}/document {update_existing: bool}` → 201 `{id, revision, version_id,
  folder, output_folder, export_error}`.

- [ ] **Step 1: Write the failing test**

Append to `services/admin-api/tests/test_claude_code_sessions.py` (and add `SAME_DOCUMENT_NAME` to the
`documentation_generation` import):

```python
def test_save_document_needs_confirmation_for_the_same_name_and_exports_into_the_folder(sessions):
    client, store = sessions.client, sessions.store
    sid = start(sessions)['id']
    url = f'/api/claude-code/sessions/{sid}'
    empty = client.post(url + '/document', headers=HEADERS, json={})
    assert empty.status_code == 409 and empty.json()['error']['message'] == 'There is no draft yet. Wait for the agent to finish a turn.'
    with store.connect() as conn:
        conn.execute("""UPDATE ka.agent_sessions_cc SET status='waiting',turns=1,draft=%s,draft_turn=1 WHERE id=%s""",
                     ('# Payments API\n\nRetries back off.', sid))
    first = client.post(url + '/document', headers=HEADERS, json={})
    assert first.status_code == 201, first.text
    saved = first.json()
    assert (saved['revision'], saved['folder'], saved['export_error']) == (1, 'payments-api-docs', None)
    document = client.get(f"/api/documentation/documents/{saved['id']}", headers=HEADERS).json()
    assert (document['title'], document['slug'], document['folder'], document['code_project_ids'], document['purpose']) == (
        'Payments – API reference', 'payments-api-reference', 'payments-api-docs', [sessions.repo], 'code')
    readme = sessions.data / 'generated-documents/payments-api-docs/payments-api-reference/README.md'
    assert readme.read_text() == '# Payments API\n\nRetries back off.'
    assert saved['output_folder'] == str(readme.parent)
    detail = client.get(url, headers=HEADERS).json()
    assert (detail['document_id'], detail['document_title']) == (saved['id'], 'Payments – API reference')
    again = client.post(url + '/document', headers=HEADERS, json={})
    assert again.status_code == 409 and again.json()['error']['message'] == SAME_DOCUMENT_NAME
    with store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions_cc SET draft='# Payments API\n\nShorter.' WHERE id=%s", (sid,))
    second = client.post(url + '/document', headers=HEADERS, json={'update_existing': True})
    assert second.status_code == 201 and (second.json()['id'], second.json()['revision']) == (saved['id'], 2)
    assert readme.read_text() == '# Payments API\n\nShorter.'
    # Another session with the same name and folder needs the confirmation too; a folder-only session is 'explanation'.
    other = start(sessions, code_project_ids=[])['id']
    with store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions_cc SET status='waiting',turns=1,draft='# Other' WHERE id=%s", (other,))
    assert client.post(f'/api/claude-code/sessions/{other}/document', headers=HEADERS, json={}).status_code == 409
    renamed = start(sessions, code_project_ids=[], document_name='Jira overview')['id']
    with store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions_cc SET status='waiting',turns=1,draft='# Jira' WHERE id=%s", (renamed,))
    created = client.post(f'/api/claude-code/sessions/{renamed}/document', headers=HEADERS, json={}).json()
    overview = client.get(f"/api/documentation/documents/{created['id']}", headers=HEADERS).json()
    assert (overview['purpose'], overview['code_project_ids']) == ('explanation', [])
    assert client.post(url + '/document', json={}).status_code == 403
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_claude_code_sessions.py::test_save_document_needs_confirmation_for_the_same_name_and_exports_into_the_folder -q`
Expected: FAIL with 404 (`Not Found`) for `POST …/document` (route missing; first assertion sees 404, not 409).

- [ ] **Step 3: Implement**

In `claude_code_api.py`, change the documents and generation imports to:

```python
from knowledge_admin.documentation_generation import PROMPT_CHANGED, SAME_DOCUMENT_NAME
from knowledge_admin.documents import DocumentInput, document_slug, export_args, export_document, export_input, save_document
```

Add at module level:

```python
class SaveInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    update_existing: bool = Field(default=False, strict=True)
```

Inside `build_api`, add:

```python
    @router.post('/sessions/{session_id}/document', status_code=201)
    def save(session_id: UUID, value: SaveInput, request: Request) -> dict:
        """Save the current draft as a version of the session's document in its folder, then export it."""
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app_name)
            session = conn.execute('SELECT * FROM ka.agent_sessions_cc WHERE id=%s AND project_id=%s FOR UPDATE',
                                   (session_id, project)).fetchone()
            if session is None:
                raise HTTPException(404, 'Session not found.')
            if not (session['draft'] or '').strip():
                raise HTTPException(409, 'There is no draft yet. Wait for the agent to finish a turn.')
            actor = db.actor(conn, *request_actor(request))
            slug = document_slug(session['document_name'])
            existing = conn.execute('SELECT id,revision FROM ka.documents WHERE project_id=%s AND folder=%s AND slug=%s',
                                    (project, session['folder'], slug)).fetchone()
            if existing is not None and not value.update_existing:
                raise HTTPException(409, SAME_DOCUMENT_NAME)
            chosen = [UUID(item) for item in session['context_snapshot']['code_project_ids']]
            available = {row['id'] for row in conn.execute('''SELECT id FROM ka.repositories
                WHERE project_id=%s AND id=ANY(%s) AND archived_at IS NULL''', (project, chosen)).fetchall()}
            linked = [item for item in chosen if item in available]  # an archived project is no longer linked
            saved = save_document(conn, project, actor, DocumentInput(
                slug=slug, title=session['document_name'], purpose='code' if chosen else 'explanation',
                markdown=session['draft'], code_project_ids=linked,
                revision=existing['revision'] if existing is not None else None),
                existing['id'] if existing is not None else None, folder=session['folder'])
            conn.execute('UPDATE ka.agent_sessions_cc SET document_id=%s,last_activity_at=now() WHERE id=%s',
                         (saved['id'], session_id))
            audit(conn, actor, 'claude_code.session.document_saved', 'agent_session',
                  {'document_id': str(saved['id']), 'revision': saved['revision']}, session_id)
            try:
                output, export_error = export_document(**export_args(export_input(conn, saved['id']))), None
            except (OSError, ValueError):
                output = None
                export_error = 'The document was saved. File export failed; retry Export to folder from the document page.'
        return jsonable_encoder({**saved, 'folder': session['folder'], 'output_folder': output, 'export_error': export_error})
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_claude_code_sessions.py tests/test_documents.py tests/test_project_documentation.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/claude_code_api.py services/admin-api/tests/test_claude_code_sessions.py
git commit -m "feat(admin-api): save a Claude Code session draft as a document in its folder

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---
### Task 6: Worker building blocks — options, credentials, safe errors, turn events and the draft

**Files:**
- Create: `services/admin-api/src/knowledge_admin/claude_code_worker.py`
- Create: `services/admin-api/tests/claude_code_fakes.py`
- Create: `services/admin-api/tests/test_claude_code_worker.py`

**Interfaces:**
- Consumes: `knowledge_llm.RuntimeModel`, `knowledge_llm.claude_model`, `knowledge_admin.agent_activity.redact`,
  `knowledge_admin.claude_code.ALLOWED_TOOLS`, `DISALLOWED_TOOLS`.
- Produces, in `knowledge_admin.claude_code_worker`: `BASE_ENV`, `PREVIEW_LINES = 20`,
  `credential_env(model) -> dict[str, str]`, `build_options(session, model) -> dict` (keyword arguments for
  `ClaudeAgentOptions`), `safe_error(text, secrets) -> str`, `result_text(content) -> str`,
  `tool_summary(name, arguments) -> str`, `usage_payload(result) -> dict`, `result_error(result) -> str`, and
  `Turn(recorder)` with `add(message)`, `draft`, `usage`, `result`, `session_id`. Messages are recognised by class name
  (`AssistantMessage`, `UserMessage`, `SystemMessage`, `ResultMessage`; blocks `TextBlock`, `ThinkingBlock`,
  `ToolUseBlock`, `ToolResultBlock`), so tests use fakes with the same names and fields.

- [ ] **Step 1: Write the fakes and the failing tests**

Create `services/admin-api/tests/claude_code_fakes.py`:

```python
"""Stand-ins for claude_agent_sdk message and block types: the worker recognises them by class name and fields."""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TextBlock:
    text: str


@dataclass
class ThinkingBlock:
    thinking: str
    signature: str = ''


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class ToolResultBlock:
    tool_use_id: str
    content: Any = None
    is_error: bool | None = None


@dataclass
class AssistantMessage:
    content: list[Any]
    model: str = 'claude'
    error: str | None = None


@dataclass
class UserMessage:
    content: Any


@dataclass
class SystemMessage:
    subtype: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ResultMessage:
    subtype: str
    is_error: bool
    session_id: str
    result: str | None = None
    usage: dict[str, Any] | None = None
    total_cost_usd: float | None = None
    errors: list[str] | None = None


class Recorder:
    """Collects run events like RunRecorder.add."""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def add(self, kind: str, payload: dict) -> None:
        self.events.append((kind, payload))
```

Create `services/admin-api/tests/test_claude_code_worker.py`:

```python
"""Claude Code worker building blocks: the fixed options, credentials only in the CLI environment, safe errors, run
events and the draft of a turn."""
from dataclasses import replace
from uuid import uuid4

import pytest
from knowledge_llm import RuntimeModel

from claude_code_fakes import (AssistantMessage, Recorder, ResultMessage, SystemMessage, TextBlock, ThinkingBlock,
                               ToolResultBlock, ToolUseBlock, UserMessage)
from knowledge_admin import claude_code_worker as worker
from knowledge_admin.claude_code import SYSTEM_PROMPT

BEDROCK = RuntimeModel(id=uuid4(), name='Claude', provider='bedrock', model='eu.anthropic.claude-sonnet-4-5-20250929-v1:0',
                       api_format='native', base_url=None, aws_region='eu-central-1', api_key='bedrock-secret-token')
ANTHROPIC = RuntimeModel(id=uuid4(), name='Claude API', provider='anthropic', model='claude-sonnet-4-5', api_format='native',
                         base_url='https://api.anthropic.com/v1', aws_region=None, api_key='sk-ant-secret')
SESSION = {'context_snapshot': {'folders': [
               {'path': '/work/data/code/app', 'kind': 'code_project', 'name': 'Payments API', 'code_project_id': 'p'},
               {'path': '/work/data/jira', 'kind': 'folder', 'name': 'jira'}]},
           'prompt_snapshot': {'system_prompt': SYSTEM_PROMPT}, 'claude_session_id': None}


def test_options_allow_only_read_tools_and_bash_in_the_chosen_folders(monkeypatch):
    monkeypatch.setenv('CLAUDE_CODE_CLI', '/services/admin-api/.venv/bin/knowledge-claude-cli')
    options = worker.build_options(SESSION, BEDROCK)
    assert options['system_prompt'] == {'type': 'preset', 'preset': 'claude_code', 'append': SYSTEM_PROMPT}
    assert options['tools'] == options['allowed_tools'] == ['Read', 'Grep', 'Glob', 'Bash']
    assert {'Edit', 'Write', 'NotebookEdit', 'Agent', 'Task', 'Skill', 'WebFetch', 'WebSearch'} <= set(options['disallowed_tools'])
    assert (options['permission_mode'], options['setting_sources'], options['skills'], options['mcp_servers'],
            options['strict_mcp_config']) == ('dontAsk', [], [], {}, True)
    assert (options['cwd'], options['add_dirs']) == ('/work/data/code/app', ['/work/data/code/app', '/work/data/jira'])
    assert (options['model'], options['resume']) == (BEDROCK.model, None)
    assert options['cli_path'] == '/services/admin-api/.venv/bin/knowledge-claude-cli'
    assert not {'max_turns', 'max_budget_usd', 'agents', 'hooks', 'user'} & set(options)  # no limits, sub-agents or hooks
    assert worker.build_options({**SESSION, 'claude_session_id': 'cli-session'}, BEDROCK)['resume'] == 'cli-session'
    monkeypatch.delenv('CLAUDE_CODE_CLI')
    assert 'cli_path' not in worker.build_options(SESSION, BEDROCK)


def test_credentials_reach_only_the_cli_environment():
    assert worker.build_options(SESSION, BEDROCK)['env'] == {
        **worker.BASE_ENV, 'CLAUDE_CODE_USE_BEDROCK': '1', 'AWS_REGION': 'eu-central-1',
        'AWS_BEARER_TOKEN_BEDROCK': 'bedrock-secret-token', 'ANTHROPIC_MODEL': BEDROCK.model}
    env = worker.build_options(SESSION, ANTHROPIC)['env']
    assert (env['ANTHROPIC_API_KEY'], env['ANTHROPIC_BASE_URL'], env['ANTHROPIC_MODEL']) == (
        'sk-ant-secret', 'https://api.anthropic.com', 'claude-sonnet-4-5')
    assert 'CLAUDE_CODE_USE_BEDROCK' not in env and 'AWS_BEARER_TOKEN_BEDROCK' not in env
    assert worker.BASE_ENV == {'HOME': '/home/claude', 'TMPDIR': '/home/claude/tmp', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
                               'DISABLE_AUTOUPDATER': '1', 'DISABLE_TELEMETRY': '1', 'DISABLE_ERROR_REPORTING': '1'}
    options = worker.build_options(SESSION, BEDROCK)
    assert 'bedrock-secret-token' not in repr({key: value for key, value in options.items() if key != 'env'})
    for model in (replace(BEDROCK, model='amazon.nova-pro-v1:0'), replace(ANTHROPIC, api_key=None),
                  replace(BEDROCK, provider='openai', api_format='responses')):
        with pytest.raises(ValueError):
            worker.credential_env(model)


def test_provider_errors_are_made_safe_with_a_hint():
    message = worker.safe_error('ValidationException: Invocation of model ID anthropic.claude-sonnet-4-5 with on-demand '
                                'throughput isn’t supported. Retry with an inference profile. token=bedrock-secret-token',
                                ['bedrock-secret-token'])
    assert 'bedrock-secret-token' not in message and '[redacted]' in message
    assert message.endswith('Bedrock needs an inference profile ID for this model, such as '
                            'eu.anthropic.claude-sonnet-4-5-20250929-v1:0. Change the model ID in LLM Settings.')
    assert worker.safe_error('AccessDeniedException: no access', []).endswith(
        'Check the key and model access of this connection in LLM Settings.')
    assert worker.safe_error('x' * 5000, []) == 'x' * 500 + ' Check the model connection in LLM Settings, then send a message to retry.'
    assert worker.safe_error('', []).startswith('Claude Code stopped with an error. ')
    assert worker.safe_error('a\n\n   b', []).startswith('a b ')


def test_turn_messages_become_run_events_and_the_final_answer_is_the_draft():
    recorder = Recorder()
    turn = worker.Turn(recorder)
    bash_output = '\n'.join(f'line {n}' for n in range(30))
    for message in (
            SystemMessage('init', {'session_id': 'cli-session'}),
            AssistantMessage([ThinkingBlock('Look at the routes first.'),
                              ToolUseBlock('t1', 'Read', {'file_path': '/work/data/code/app/main.py'}),
                              ToolUseBlock('t2', 'Grep', {'pattern': 'retry', 'path': '/work/data/code/app'}),
                              ToolUseBlock('t3', 'Glob', {'pattern': '**/*.py'}),
                              ToolUseBlock('t4', 'Bash', {'command': 'ls -la /work/data/jira'})]),
            UserMessage([ToolResultBlock('t1', 'import app'),
                         ToolResultBlock('t4', [{'type': 'text', 'text': bash_output}], is_error=False)]),
            UserMessage('a plain user echo is ignored'),
            AssistantMessage([TextBlock('Draft below.'), TextBlock('   '), TextBlock('# Payments API')]),
            ResultMessage('success', False, 'cli-session', result='# Payments API\n\nRetries back off.',
                          usage={'input_tokens': 100, 'cache_read_input_tokens': 900, 'cache_creation_input_tokens': 50,
                                 'output_tokens': 40}, total_cost_usd=0.12)):
        turn.add(message)
    assert [kind for kind, _ in recorder.events] == ['reasoning', 'tool_call', 'tool_call', 'tool_call', 'tool_call',
                                                     'tool_result', 'tool_result', 'message', 'message', 'usage']
    calls = [payload for kind, payload in recorder.events if kind == 'tool_call']
    assert [(c['name'], c['call_id'], c['summary']) for c in calls] == [
        ('Read', 't1', '/work/data/code/app/main.py'), ('Grep', 't2', 'retry in /work/data/code/app'),
        ('Glob', 't3', '**/*.py'), ('Bash', 't4', 'ls -la /work/data/jira')]
    assert calls[0]['arguments'] == {'file_path': '/work/data/code/app/main.py'}
    read, bash = [payload for kind, payload in recorder.events if kind == 'tool_result']
    assert (read['name'], read['output'], read['is_error']) == ('Read', 'import app', False)
    assert bash['name'] == 'Bash' and bash['output'] == bash_output
    assert bash['preview'] == '\n'.join(f'line {n}' for n in range(20))
    assert recorder.events[0] == ('reasoning', {'text': 'Look at the routes first.'})
    assert recorder.events[-1] == ('usage', {'input_tokens': 1050, 'output_tokens': 40, 'cached_tokens': 900,
                                             'total_tokens': 1090, 'cost_usd': 0.12})
    assert (turn.draft, turn.session_id, turn.usage['output_tokens']) == ('# Payments API\n\nRetries back off.', 'cli-session', 40)
    fallback = worker.Turn(Recorder())
    fallback.add(AssistantMessage([TextBlock('Only text')]))
    fallback.add(ResultMessage('success', False, 's', result=''))
    assert fallback.draft == 'Only text'
    failed = Recorder()
    worker.Turn(failed).add(AssistantMessage([], error='rate_limit'))
    assert failed.events == [('error', {'message': 'The model reported an error: rate_limit'})]
    assert worker.result_error(None) == 'Claude Code ended the turn without a result.'
    assert worker.result_error(ResultMessage('error_during_execution', True, 's', result='Overloaded', errors=['retry later'])) == 'Overloaded retry later'
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_claude_code_worker.py -q`
Expected: FAIL with `ImportError: cannot import name 'claude_code_worker' from 'knowledge_admin'`.

- [ ] **Step 3: Create `services/admin-api/src/knowledge_admin/claude_code_worker.py`**

```python
"""Claude Code worker: runs interactive documentation sessions with the Claude Agent SDK, one client per session.

The model key is decrypted here and passed only in the CLI process environment. Nothing imports claude_agent_sdk at
module import time; messages are recognised by class name, so tests drive the worker with same-named fakes."""
import logging
import os
from collections.abc import Iterable
from typing import Any

from knowledge_llm import RuntimeModel, claude_model

from knowledge_admin.agent_activity import redact
from knowledge_admin.claude_code import ALLOWED_TOOLS, DISALLOWED_TOOLS

logger = logging.getLogger(__name__)

PREVIEW_LINES = 20
BASE_ENV = {'HOME': '/home/claude', 'TMPDIR': '/home/claude/tmp', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
            'DISABLE_AUTOUPDATER': '1', 'DISABLE_TELEMETRY': '1', 'DISABLE_ERROR_REPORTING': '1'}
HINTS = (
    (('inference profile', 'on-demand throughput'),
     'Bedrock needs an inference profile ID for this model, such as eu.anthropic.claude-sonnet-4-5-20250929-v1:0. '
     'Change the model ID in LLM Settings.'),
    (('403', '401', 'accessdenied', 'authentication'), 'Check the key and model access of this connection in LLM Settings.'),
)
DEFAULT_HINT = 'Check the model connection in LLM Settings, then send a message to retry.'


def credential_env(model: RuntimeModel) -> dict[str, str]:
    """The CLI environment variables that carry this connection's key; nothing else ever receives the key."""
    if not claude_model(model.provider, model.api_format, model.model):
        raise ValueError('The session model is not a Claude connection. Start a new session with a Claude model.')
    if not model.api_key:
        raise ValueError('The model connection has no key. Save its key in LLM Settings.')
    if model.provider == 'bedrock':
        return {'CLAUDE_CODE_USE_BEDROCK': '1', 'AWS_REGION': model.aws_region or '',
                'AWS_BEARER_TOKEN_BEDROCK': model.api_key, 'ANTHROPIC_MODEL': model.model}
    env = {'ANTHROPIC_API_KEY': model.api_key, 'ANTHROPIC_MODEL': model.model}
    if model.base_url:
        base = model.base_url.rstrip('/')
        env['ANTHROPIC_BASE_URL'] = base[:-3] if base.endswith('/v1') else base
    return env


def build_options(session: dict[str, Any], model: RuntimeModel) -> dict[str, Any]:
    """Keyword arguments for ClaudeAgentOptions: fixed read-only tools plus Bash, the chosen folders, no limits."""
    folders = [folder['path'] for folder in session['context_snapshot']['folders']]
    options = {
        'system_prompt': {'type': 'preset', 'preset': 'claude_code', 'append': session['prompt_snapshot']['system_prompt']},
        'tools': list(ALLOWED_TOOLS), 'allowed_tools': list(ALLOWED_TOOLS), 'disallowed_tools': list(DISALLOWED_TOOLS),
        'permission_mode': 'dontAsk', 'setting_sources': [], 'skills': [], 'mcp_servers': {}, 'strict_mcp_config': True,
        'cwd': folders[0], 'add_dirs': folders, 'model': model.model, 'env': {**BASE_ENV, **credential_env(model)},
        'resume': session['claude_session_id'],
    }
    if os.environ.get('CLAUDE_CODE_CLI'):
        options['cli_path'] = os.environ['CLAUDE_CODE_CLI']
    return options


def safe_error(text: str, secrets: Iterable[str]) -> str:
    """A provider or CLI error for people: redacted, one line, at most 500 characters, with a hint."""
    reason = ' '.join(redact(text or '', [secret for secret in secrets if secret]).split())[:500]
    reason = reason or 'Claude Code stopped with an error.'
    lowered = reason.lower()
    hint = next((hint for needles, hint in HINTS if any(needle in lowered for needle in needles)), DEFAULT_HINT)
    return f'{reason} {hint}'


def result_text(content: Any) -> str:
    if content is None:
        return ''
    if isinstance(content, str):
        return content
    return '\n'.join(item.get('text', '') for item in content if isinstance(item, dict) and item.get('type') == 'text')


def tool_summary(name: str, arguments: dict[str, Any]) -> str:
    if name == 'Read':
        return str(arguments.get('file_path', ''))
    if name in ('Grep', 'Glob'):
        pattern, path = str(arguments.get('pattern', '')), arguments.get('path')
        return f'{pattern} in {path}' if path else pattern
    if name == 'Bash':
        return str(arguments.get('command', ''))
    return ''


def usage_payload(result: Any) -> dict[str, Any]:
    usage = result.usage or {}
    cached = int(usage.get('cache_read_input_tokens') or 0)
    prompt = int(usage.get('input_tokens') or 0) + cached + int(usage.get('cache_creation_input_tokens') or 0)
    output = int(usage.get('output_tokens') or 0)
    return {'input_tokens': prompt, 'output_tokens': output, 'cached_tokens': cached, 'total_tokens': prompt + output,
            'cost_usd': float(result.total_cost_usd or 0)}


def result_error(result: Any) -> str:
    if result is None:
        return 'Claude Code ended the turn without a result.'
    return ' '.join(part for part in [result.result or '', *(getattr(result, 'errors', None) or [])] if part)


class Turn:
    """One turn's SDK messages as run record events, plus its draft, usage and CLI session id."""

    def __init__(self, recorder):
        self.recorder = recorder
        self.tools: dict[str, str] = {}
        self.texts: list[str] = []
        self.result = None
        self.usage: dict[str, Any] | None = None
        self.session_id: str | None = None

    def add(self, message: Any) -> None:
        kind = type(message).__name__
        if kind == 'SystemMessage' and message.subtype == 'init':
            self.session_id = message.data.get('session_id') or self.session_id
        elif kind == 'AssistantMessage':
            for block in message.content:
                self._block(block)
            if getattr(message, 'error', None):
                self.recorder.add('error', {'message': f'The model reported an error: {message.error}'})
        elif kind == 'UserMessage' and isinstance(message.content, list):
            for block in message.content:
                if type(block).__name__ == 'ToolResultBlock':
                    output = result_text(block.content)
                    self.recorder.add('tool_result', {
                        'name': self.tools.get(block.tool_use_id, ''), 'call_id': block.tool_use_id, 'output': output,
                        'preview': '\n'.join(output.splitlines()[:PREVIEW_LINES]), 'is_error': bool(block.is_error)})
        elif kind == 'ResultMessage':
            self.result = message
            self.session_id = message.session_id or self.session_id
            self.usage = usage_payload(message)
            self.recorder.add('usage', self.usage)

    def _block(self, block: Any) -> None:
        name = type(block).__name__
        if name == 'TextBlock' and block.text.strip():
            self.texts.append(block.text)
            self.recorder.add('message', {'text': block.text})
        elif name == 'ThinkingBlock' and block.thinking.strip():
            self.recorder.add('reasoning', {'text': block.thinking})
        elif name == 'ToolUseBlock':
            self.tools[block.id] = block.name
            self.recorder.add('tool_call', {'name': block.name, 'call_id': block.id, 'arguments': block.input,
                                            'summary': tool_summary(block.name, block.input)})

    @property
    def draft(self) -> str:
        final = (self.result.result if self.result is not None else None) or ''
        return final if final.strip() else (self.texts[-1] if self.texts else '')
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_claude_code_worker.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/claude_code_worker.py services/admin-api/tests/claude_code_fakes.py \
  services/admin-api/tests/test_claude_code_worker.py
git commit -m "feat(admin-api): Claude Code options, CLI credentials, safe errors and turn events

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Worker — session runner, claims and leases, Stop and Close, the CLI user switch

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/claude_code_worker.py`
- Create: `services/admin-api/src/knowledge_admin/claude_code_cli.py`
- Modify: `services/admin-api/tests/claude_code_fakes.py` (add `FakeClient`)
- Create: `services/admin-api/tests/test_claude_code_runner.py`

**Interfaces:**
- Consumes: Task 3's tables and API (the tests send messages, Stop and Close through it); Task 6's helpers;
  `RunRecorder` (`knowledge_admin.run_record`); `decode_model`; `close_idle`.
- Produces, in `knowledge_admin.claude_code_worker`: `LEASE_SECONDS = 60`, `IDLE_RELEASE_SECONDS = 300.0`,
  `INTERRUPT_GRACE_SECONDS = 30.0`, `STOPPED`, `INTERRUPTED`, `sdk_client(options)`,
  `claim(store, worker_id, active, free) -> list[dict]` (rows are `ka.agent_sessions_cc.*` plus `previous` status and
  `agent_name`), `SessionRunner(store, session, client_factory=sdk_client, *, poll=1.0, idle_release=300.0)` with
  `async run()`, `async serve(store, client_factory=sdk_client, *, stopped, worker_id=None, limit=None, poll=1.0,
  idle_release=300.0)`, `secrets_from_environment() -> tuple[bytes, str]`, `main()`.
- Produces, in `knowledge_admin.claude_code_cli`: `USER = 'claude'`, `bundled_cli() -> str`,
  `main(argv=None, *, os_module=os)` (Task 8 registers both scripts).

- [ ] **Step 1: Add the fake client and write the failing tests**

Append to `services/admin-api/tests/claude_code_fakes.py` (and add `import asyncio` at the top):

```python
class FakeClient:
    """A ClaudeSDKClient stand-in that is also its factory. Each query plays the next scripted turn: its items are
    yielded in order and the last one is the ResultMessage, or an exception to raise. A turn listed in `gates` waits
    before its last item until the gate opens or interrupt() is called; an interrupted turn ends with an error
    ResultMessage, as the CLI does."""

    def __init__(self, turns: list[list], gates: dict[int, asyncio.Event] | None = None):
        self.turns, self.gates = turns, gates or {}
        self.options: list[dict] = []
        self.queries: list[str] = []
        self.interrupts = 0
        self.entered = self.exited = 0
        self.interrupted = asyncio.Event()

    def __call__(self, options: dict):
        self.options.append(options)
        return self

    async def __aenter__(self):
        self.entered += 1
        return self

    async def __aexit__(self, *exc):
        self.exited += 1
        return False

    async def query(self, text: str) -> None:
        self.queries.append(text)

    async def interrupt(self) -> None:
        self.interrupts += 1
        self.interrupted.set()

    async def receive_response(self):
        index = len(self.queries) - 1
        *messages, last = self.turns[index]
        for message in messages:
            yield message
        if index in self.gates:
            gate = asyncio.ensure_future(self.gates[index].wait())
            stop = asyncio.ensure_future(self.interrupted.wait())
            await asyncio.wait({gate, stop}, return_when=asyncio.FIRST_COMPLETED)
            gate.cancel()
            stop.cancel()
            if self.interrupted.is_set():
                self.interrupted.clear()
                yield ResultMessage('error_during_execution', True, 'cli-session', result='')
                return
        if isinstance(last, BaseException):
            raise last
        yield last
```

Create `services/admin-api/tests/test_claude_code_runner.py`:

```python
"""The Claude Code worker with a fake ClaudeSDKClient: turns, queued messages, Stop, Close, failures and leases."""
import asyncio
import base64
import json
import logging
import os
import time
from types import SimpleNamespace
from uuid import UUID

import pytest

from claude_code_fakes import AssistantMessage, FakeClient, ResultMessage, TextBlock, ToolResultBlock, ToolUseBlock, UserMessage
from knowledge_admin import claude_code_cli as cli
from knowledge_admin import claude_code_worker as worker
from knowledge_admin import run_record
from test_claude_code_sessions import sessions, start  # noqa: F401  (fixture)
from test_repositories import HEADERS

KEY = 'bedrock-secret-token'


def done(text: str, **usage) -> ResultMessage:
    return ResultMessage('success', False, 'cli-session', result=text, usage=usage or None)


@pytest.fixture
def started(sessions, monkeypatch):
    monkeypatch.setattr(run_record, '_store', sessions.store)
    return sessions, UUID(start(sessions)['id'])


def claimed(sessions, worker_id: str = 'worker-1') -> dict:
    [row] = worker.claim(sessions.store, worker_id, [], 4)
    return row


def post(sessions, path: str, body: dict | None = None):
    return asyncio.to_thread(sessions.client.post, path, headers=HEADERS, json=body)


def session_row(store, session_id) -> dict:
    with store.connect() as conn:
        return conn.execute('SELECT * FROM ka.agent_sessions_cc WHERE id=%s', (session_id,)).fetchone()


async def wait_for(store, session_id, predicate, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        row = session_row(store, session_id)
        if predicate(row):
            return row
        if time.monotonic() > deadline:
            raise AssertionError(f"Session state not reached: {row['status']}, turn {row['turns']}")
        await asyncio.sleep(0.02)


def events(store, session_id) -> list[dict]:
    with store.connect() as conn:
        return conn.execute('''SELECT run_key,seq,kind,payload FROM ka.agent_run_events WHERE starts_with(run_key,%s)
            ORDER BY id''', (f'cc:{session_id}:',)).fetchall()


def test_draft_is_the_final_answer_of_each_completed_turn(started):
    sessions, sid = started
    store, url = sessions.store, f'/api/claude-code/sessions/{sid}'
    client = FakeClient([
        [AssistantMessage([ToolUseBlock('t1', 'Read', {'file_path': '/work/data/code/app/main.py'})]),
         UserMessage([ToolResultBlock('t1', 'import app')]), AssistantMessage([TextBlock('# Payments API')]),
         done('# Payments API', input_tokens=10, output_tokens=5)],
        [AssistantMessage([TextBlock('Working on it')]),
         ResultMessage('error_during_execution', True, 'cli-session', result='Model overloaded')],
        [AssistantMessage([TextBlock('# Payments API\n\nShort.')]), done('# Payments API\n\nShort.', input_tokens=20, output_tokens=6)]])

    async def scenario():
        stopped = asyncio.Event()
        serving = asyncio.create_task(worker.serve(store, client, stopped=stopped, worker_id='worker-1', limit=2, poll=0.02))
        first = await wait_for(store, sid, lambda s: s['status'] == 'waiting' and s['turns'] == 1)
        assert (first['draft'], first['draft_turn'], first['worker_id']) == ('# Payments API', 1, 'worker-1')
        await post(sessions, url + '/messages', {'text': 'Shorten it.'})
        failed = await wait_for(store, sid, lambda s: s['status'] == 'failed' and s['turns'] == 2)
        assert failed['draft'] == '# Payments API'  # a failed turn keeps the previous draft
        assert failed['status_message'].startswith('Model overloaded ')
        await post(sessions, url + '/messages', {'text': 'Try again.'})
        last = await wait_for(store, sid, lambda s: s['status'] == 'waiting' and s['turns'] == 3)
        assert (last['draft'], last['draft_turn']) == ('# Payments API\n\nShort.', 3)
        await post(sessions, url + '/close')
        await wait_for(store, sid, lambda s: s['worker_id'] is None)
        stopped.set()
        await asyncio.wait_for(serving, 5)

    asyncio.run(scenario())
    assert client.queries[1:] == ['Shorten it.', 'Try again.']
    assert client.entered == client.exited == 1  # one client for the whole session, released on close
    row = session_row(store, sid)
    assert (row['status'], row['claude_session_id'], row['input_tokens'], row['output_tokens'], row['lease_expires_at']) == (
        'closed', 'cli-session', 30, 11, None)
    recorded = events(store, sid)
    assert [e['kind'] for e in recorded if e['run_key'] == f'cc:{sid}:1'] == [
        'start', 'instructions', 'input', 'tool_call', 'tool_result', 'message', 'usage', 'end']
    assert [e['kind'] for e in recorded if e['run_key'] == f'cc:{sid}:3'] == ['start', 'input', 'message', 'usage', 'end']
    runs = sessions.client.get('/api/agent-runs?kind=claude_code', headers=HEADERS).json()['runs']
    assert [(r['run_key'], r['status'], r['agent']) for r in runs] == [
        (f'cc:{sid}:3', 'completed', 'Technical writer'), (f'cc:{sid}:2', 'failed', 'Technical writer'),
        (f'cc:{sid}:1', 'completed', 'Technical writer')]


def test_queued_message_is_delivered_once_as_the_next_turn(started):
    sessions, sid = started
    store, url = sessions.store, f'/api/claude-code/sessions/{sid}'
    gate = asyncio.Event()
    client = FakeClient([[AssistantMessage([TextBlock('Reading')]), done('# Draft one')], [done('# Draft two')]], gates={0: gate})

    async def scenario():
        runner = asyncio.create_task(worker.SessionRunner(store, claimed(sessions), client, poll=0.02).run())
        await wait_for(store, sid, lambda s: s['status'] == 'working')
        for text in ('Also cover retries.', 'And the error codes.'):
            assert (await post(sessions, url + '/messages', {'text': text})).json()['working'] is True
        await asyncio.sleep(0.1)
        assert len(client.queries) == 1  # nothing reaches the agent while it responds
        gate.set()
        await wait_for(store, sid, lambda s: s['status'] == 'waiting' and s['turns'] == 2)
        await post(sessions, url + '/close')
        await asyncio.wait_for(runner, 5)

    asyncio.run(scenario())
    assert client.queries[1:] == ['Also cover retries.\n\nAnd the error codes.']
    with store.connect() as conn:
        inputs = conn.execute('SELECT seq,status,turn FROM ka.agent_session_inputs WHERE session_id=%s ORDER BY seq', (sid,)).fetchall()
    assert [(i['seq'], i['status'], i['turn']) for i in inputs] == [(1, 'delivered', 1), (2, 'delivered', 2), (3, 'delivered', 2)]
    assert session_row(store, sid)['draft'] == '# Draft two'


def test_stop_interrupts_the_turn_and_discards_queued_messages(started):
    sessions, sid = started
    store, url = sessions.store, f'/api/claude-code/sessions/{sid}'
    client = FakeClient([[AssistantMessage([TextBlock('Partial text')]), done('never reached')], [done('# After stop')]],
                        gates={0: asyncio.Event()})  # the gate never opens: only Stop ends turn 1

    async def scenario():
        runner = asyncio.create_task(worker.SessionRunner(store, claimed(sessions), client, poll=0.02).run())
        await wait_for(store, sid, lambda s: s['status'] == 'working')
        await post(sessions, url + '/messages', {'text': 'Queued before stop'})
        assert (await post(sessions, url + '/stop')).json()['discarded'] == 1
        row = await wait_for(store, sid, lambda s: s['status'] == 'stopped')
        assert (row['status_message'], row['draft'], row['stop_requested_at']) == (worker.STOPPED, None, None)
        await asyncio.sleep(0.1)
        assert len(client.queries) == 1  # the discarded message never starts a turn
        await post(sessions, url + '/messages', {'text': 'Continue please'})
        await wait_for(store, sid, lambda s: s['status'] == 'waiting' and s['turns'] == 2)
        await post(sessions, url + '/close')
        await asyncio.wait_for(runner, 5)

    asyncio.run(scenario())
    assert (client.interrupts, client.queries[1:]) == (1, ['Continue please'])
    assert [e['payload'] for e in events(store, sid) if e['kind'] == 'end'] == [{'status': 'stopped'}, {'status': 'completed'}]
    assert (session_row(store, sid)['draft'], session_row(store, sid)['draft_turn']) == ('# After stop', 2)


def test_close_ends_the_session_and_releases_the_worker(started):
    sessions, sid = started
    store = sessions.store
    client = FakeClient([[AssistantMessage([TextBlock('Partial')]), done('never reached')]], gates={0: asyncio.Event()})

    async def scenario():
        runner = asyncio.create_task(worker.SessionRunner(store, claimed(sessions), client, poll=0.02).run())
        await wait_for(store, sid, lambda s: s['status'] == 'working')
        await post(sessions, f'/api/claude-code/sessions/{sid}/close')
        await asyncio.wait_for(runner, 5)

    asyncio.run(scenario())
    assert (client.interrupts, client.entered, client.exited) == (1, 1, 1)
    row = session_row(store, sid)
    assert (row['status'], row['status_message'], row['worker_id'], row['lease_expires_at'], row['draft']) == (
        'closed', 'Closed.', None, None, None)
    assert [e['payload'] for e in events(store, sid) if e['kind'] == 'end'] == [{'status': 'stopped'}]


def test_no_key_in_events_logs_or_status(started, caplog):
    caplog.set_level(logging.DEBUG)
    sessions, sid = started
    store, url = sessions.store, f'/api/claude-code/sessions/{sid}'
    client = FakeClient([[RuntimeError(f'AccessDeniedException: bearer {KEY} may not invoke this model')],
                         [AssistantMessage([TextBlock(f'Never print {KEY}')]), done(f'# Draft without {KEY}')]])

    async def scenario():
        runner = asyncio.create_task(worker.SessionRunner(store, claimed(sessions), client, poll=0.02).run())
        failed = await wait_for(store, sid, lambda s: s['status'] == 'failed')
        assert failed['status_message'].endswith('Check the key and model access of this connection in LLM Settings.')
        assert KEY not in failed['status_message']
        await post(sessions, url + '/messages', {'text': 'Try again.'})
        await wait_for(store, sid, lambda s: s['status'] == 'waiting' and s['turns'] == 2)
        await post(sessions, url + '/close')
        await asyncio.wait_for(runner, 5)

    asyncio.run(scenario())
    assert (client.entered, client.exited) == (2, 2)  # a failed client is dropped and opened again
    env = client.options[0]['env']
    assert (env['AWS_BEARER_TOKEN_BEDROCK'], env['CLAUDE_CODE_USE_BEDROCK'], env['AWS_REGION']) == (KEY, '1', 'eu-central-1')
    recorded = json.dumps(events(store, sid))
    row = session_row(store, sid)
    for text in (recorded, json.dumps(row, default=str), caplog.text):
        assert KEY not in text
    assert '[redacted]' in recorded and row['draft'] == '# Draft without [redacted]'


def test_idle_session_releases_its_client_and_resumes_on_the_next_message(started):
    sessions, sid = started
    store = sessions.store
    client = FakeClient([[done('# One')], [done('# Two')]])

    async def scenario():
        await asyncio.wait_for(worker.SessionRunner(store, claimed(sessions), client, poll=0.02, idle_release=0.1).run(), 5)
        row = session_row(store, sid)
        assert (row['status'], row['worker_id']) == ('waiting', None)
        assert worker.claim(store, 'worker-2', [], 4) == []  # nothing queued: no worker needed
        await post(sessions, f'/api/claude-code/sessions/{sid}/messages', {'text': 'Shorten it.'})
        runner = worker.SessionRunner(store, claimed(sessions, 'worker-2'), client, poll=0.02, idle_release=0.1)
        await asyncio.wait_for(runner.run(), 5)

    asyncio.run(scenario())
    assert (client.entered, client.exited) == (2, 2)
    assert [options['resume'] for options in client.options] == [None, 'cli-session']
    assert session_row(store, sid)['draft'] == '# Two'


def test_expired_lease_is_reclaimed_and_the_interrupted_turn_fails(started):
    sessions, sid = started
    store = sessions.store
    first = claimed(sessions)
    assert (first['worker_id'], first['previous'], first['agent_name']) == ('worker-1', 'starting', 'Technical writer')
    assert worker.claim(store, 'worker-2', [], 4) == []  # leased by worker-1
    with store.connect() as conn:  # worker-1 started turn 1, then died without finishing it
        conn.execute("UPDATE ka.agent_session_inputs SET status='delivered',turn=1,delivered_at=now() WHERE session_id=%s", (sid,))
        conn.execute("UPDATE ka.agent_sessions_cc SET status='working',turns=1 WHERE id=%s", (sid,))
        conn.execute('''INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,payload)
            SELECT project_id,%s,1,'start',agent_id,'{"kind": "claude_code"}' FROM ka.agent_sessions_cc WHERE id=%s''', (f'cc:{sid}:1', sid))
    assert worker.claim(store, 'worker-1', [sid], 0) == []  # the heartbeat renews worker-1's lease
    with store.connect() as conn:
        assert conn.execute("SELECT lease_expires_at > now() + interval '50 seconds' AS live FROM ka.agent_sessions_cc WHERE id=%s",
                            (sid,)).fetchone()['live']
        conn.execute("UPDATE ka.agent_sessions_cc SET lease_expires_at=now()-interval '1 second' WHERE id=%s", (sid,))
    [row] = worker.claim(store, 'worker-2', [], 4)
    assert (row['worker_id'], row['previous'], row['status'], row['status_message']) == (
        'worker-2', 'working', 'failed', worker.INTERRUPTED)
    assert [(e['seq'], e['kind'], e['payload']) for e in events(store, sid)] == [
        (1, 'start', {'kind': 'claude_code'}), (2, 'error', {'message': worker.INTERRUPTED}), (3, 'end', {'status': 'failed'})]
    with store.connect() as conn:  # closed sessions are never claimed, even with input
        conn.execute("UPDATE ka.agent_sessions_cc SET status='closed',closed_at=now(),lease_expires_at=NULL WHERE id=%s", (sid,))
        conn.execute('''INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text)
            SELECT id,2,actor_id,'late' FROM ka.agent_sessions_cc WHERE id=%s''', (sid,))
    assert worker.claim(store, 'worker-3', [], 4) == []


def test_worker_strips_database_and_encryption_secrets_from_its_environment(monkeypatch):
    monkeypatch.setenv('CREDENTIAL_ENCRYPTION_KEY', base64.urlsafe_b64encode(b'k' * 32).decode())
    monkeypatch.setenv('PGPASSWORD', 'db-secret')
    monkeypatch.setenv('PGHOST', 'postgres')
    key, conninfo = worker.secrets_from_environment()
    assert key == b'k' * 32 and 'db-secret' in conninfo
    assert 'CREDENTIAL_ENCRYPTION_KEY' not in os.environ and 'PGPASSWORD' not in os.environ
    assert os.environ['PGHOST'] == 'postgres'


def test_cli_wrapper_drops_to_the_claude_user_before_running_the_bundled_cli(monkeypatch):
    calls = []

    class FakeOs:
        def __init__(self, uid: int):
            self.uid = uid

        def getuid(self):
            return self.uid

        def setgroups(self, groups):
            calls.append(('setgroups', groups))

        def setgid(self, gid):
            calls.append(('setgid', gid))

        def setuid(self, uid):
            calls.append(('setuid', uid))
            self.uid = uid

        def execv(self, path, argv):
            calls.append(('execv', path, argv))

    claude = SimpleNamespace(pw_uid=1001, pw_gid=1001)
    monkeypatch.setattr(cli.pwd, 'getpwnam', lambda name: claude if name == 'claude' else None)
    monkeypatch.setattr(cli, 'bundled_cli', lambda: '/venv/claude_agent_sdk/_bundled/claude')
    cli.main(['--output-format', 'stream-json'], os_module=FakeOs(0))
    assert calls == [('setgroups', []), ('setgid', 1001), ('setuid', 1001),
                     ('execv', '/venv/claude_agent_sdk/_bundled/claude',
                      ['/venv/claude_agent_sdk/_bundled/claude', '--output-format', 'stream-json'])]
    with pytest.raises(SystemExit):  # neither root nor claude: refuse instead of running as another user
        cli.main([], os_module=FakeOs(501))
    monkeypatch.setattr(cli.pwd, 'getpwnam', lambda name: SimpleNamespace(pw_uid=0, pw_gid=0))
    with pytest.raises(SystemExit):
        cli.main([], os_module=FakeOs(0))
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_claude_code_runner.py -q`
Expected: FAIL with `ImportError: cannot import name 'claude_code_cli' from 'knowledge_admin'`.

- [ ] **Step 3: Create `services/admin-api/src/knowledge_admin/claude_code_cli.py`**

```python
"""Start the bundled Claude Code CLI as the unprivileged `claude` user.

The claude-code-worker runs as root so that only it can read its environment (database password, encryption key).
ClaudeAgentOptions.cli_path points to this script, which drops to `claude` (groups, gid, uid) before running the CLI,
so Bash in the agent cannot read the worker's process environment."""
import importlib.util
import os
import pwd
import sys
from pathlib import Path

USER = 'claude'


def bundled_cli() -> str:
    """The CLI shipped inside the claude-agent-sdk wheel, found without importing the package."""
    spec = importlib.util.find_spec('claude_agent_sdk')
    if spec is None or not spec.submodule_search_locations:
        raise SystemExit('claude-agent-sdk is not installed')
    return str(Path(next(iter(spec.submodule_search_locations))) / '_bundled' / 'claude')


def main(argv: list[str] | None = None, *, os_module=os) -> None:
    user = pwd.getpwnam(USER)
    if user.pw_uid == 0:
        raise SystemExit(f'The {USER} user must not be root')
    if os_module.getuid() == 0:
        os_module.setgroups([])
        os_module.setgid(user.pw_gid)
        os_module.setuid(user.pw_uid)
    if os_module.getuid() != user.pw_uid:
        raise SystemExit(f'Claude Code must run as {USER}')
    target = bundled_cli()
    os_module.execv(target, [target, *(sys.argv[1:] if argv is None else argv)])
```

- [ ] **Step 4: Add the runner, claims, serve and main to `claude_code_worker.py`**

Replace the import block with:

```python
import asyncio
import base64
import logging
import os
import signal
import socket
import time
from collections import deque
from collections.abc import Iterable
from contextlib import AsyncExitStack, suppress
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from cryptography.exceptions import InvalidTag
from knowledge_llm import RuntimeModel, claude_model, decode_model
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

from knowledge_admin import run_record
from knowledge_admin.agent_activity import redact
from knowledge_admin.claude_code import ALLOWED_TOOLS, DISALLOWED_TOOLS, close_idle
from knowledge_admin.repositories import RepositoryStore
from knowledge_admin.run_record import RunRecorder
```

Add below `DEFAULT_HINT`:

```python
LEASE_SECONDS = 60
IDLE_RELEASE_SECONDS = 300.0
INTERRUPT_GRACE_SECONDS = 30.0
STOPPED = 'Stopped. Send a message to continue.'
INTERRUPTED = 'The worker restarted during this turn. Send a message to continue.'
END_STATUS = {'waiting': 'completed', 'stopped': 'stopped', 'failed': 'failed'}
CLOSED = object()
```

Append to the module:

```python
def sdk_client(options: dict[str, Any]):
    """The real client. The SDK is imported only here, when a session starts its first turn."""
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
    return ClaudeSDKClient(options=ClaudeAgentOptions(**options))


def claim(store: RepositoryStore, worker_id: str, active: list[UUID], free: int) -> list[dict]:
    """Close idle sessions, renew this worker's leases, then claim sessions that need a worker: a queued message, or a
    turn left `working` by a worker that stopped (that turn is recorded as failed)."""
    with store.connect() as conn:
        close_idle(conn)
        conn.execute("""UPDATE ka.agent_sessions_cc SET lease_expires_at=now()+%s*interval '1 second'
            WHERE worker_id=%s AND id=ANY(%s::uuid[]) AND status<>'closed'""", (LEASE_SECONDS, worker_id, active))
        if free <= 0:
            return []
        rows = conn.execute("""WITH picked AS (
                SELECT s.id,s.status AS previous FROM ka.agent_sessions_cc s
                WHERE s.status<>'closed' AND (s.lease_expires_at IS NULL OR s.lease_expires_at<now())
                  AND NOT s.id=ANY(%s::uuid[])
                  AND (s.status='working' OR EXISTS (SELECT 1 FROM ka.agent_session_inputs i
                       WHERE i.session_id=s.id AND i.status='queued'))
                ORDER BY s.created_at,s.id LIMIT %s FOR UPDATE OF s SKIP LOCKED)
            UPDATE ka.agent_sessions_cc s SET worker_id=%s,lease_expires_at=now()+%s*interval '1 second',
                status=CASE WHEN s.status='working' THEN 'failed' ELSE s.status END,
                status_message=CASE WHEN s.status='working' THEN %s ELSE s.status_message END,
                stop_requested_at=CASE WHEN s.status='working' THEN NULL ELSE s.stop_requested_at END
            FROM picked WHERE s.id=picked.id
            RETURNING s.*,picked.previous,(SELECT a.name FROM ka.agents a WHERE a.id=s.agent_id) AS agent_name""",
            (active, free, worker_id, LEASE_SECONDS, INTERRUPTED)).fetchall()
        for row in rows:
            if row['previous'] == 'working' and row['turns'] > 0:
                key = f"cc:{row['id']}:{row['turns']}"
                for kind, payload in (('error', {'message': INTERRUPTED}), ('end', {'status': 'failed'})):
                    conn.execute("""INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,actor_id,payload)
                        SELECT %s,%s,coalesce(max(seq),0)+1,%s,%s,%s,%s FROM ka.agent_run_events WHERE run_key=%s""",
                        (row['project_id'], key, kind, row['agent_id'], row['actor_id'], Jsonb(payload), key))
    return rows


class SessionRunner:
    """One session in one asyncio task: one client, created on the first turn and kept until the session closes or has
    been idle for `idle_release` seconds. This task connects, sends and interrupts; a child task reads responses."""

    def __init__(self, store: RepositoryStore, session: dict, client_factory=sdk_client, *, poll: float = 1.0,
                 idle_release: float = IDLE_RELEASE_SECONDS):
        self.store, self.session, self.client_factory = store, session, client_factory
        self.id = session['id']
        self.poll, self.idle_release = poll, idle_release
        self.secrets: list[str] = []
        self.stack: AsyncExitStack | None = None
        self.client = None
        self.stderr: deque[str] = deque(maxlen=20)

    async def run(self) -> None:
        idle_since = time.monotonic()
        try:
            while True:
                turn = await asyncio.to_thread(self.next_turn)
                if turn is CLOSED:
                    return
                if turn is None:
                    if time.monotonic() - idle_since >= self.idle_release:
                        return
                    await asyncio.sleep(self.poll)
                    continue
                await self.run_turn(*turn)
                idle_since = time.monotonic()
        finally:
            await self.disconnect()
            self.secrets.clear()
            with suppress(psycopg.Error):
                await asyncio.to_thread(self.release)

    def next_turn(self):
        """Deliver every queued message as the next turn, once; CLOSED when the session is closed."""
        with self.store.connect() as conn:
            row = conn.execute('SELECT status,turns FROM ka.agent_sessions_cc WHERE id=%s FOR UPDATE', (self.id,)).fetchone()
            if row is None or row['status'] == 'closed':
                return CLOSED
            inputs = conn.execute("""SELECT id,text FROM ka.agent_session_inputs WHERE session_id=%s AND status='queued'
                ORDER BY seq""", (self.id,)).fetchall()
            if not inputs:
                return None
            number = row['turns'] + 1
            conn.execute("""UPDATE ka.agent_session_inputs SET status='delivered',turn=%s,delivered_at=now()
                WHERE id=ANY(%s)""", (number, [item['id'] for item in inputs]))
            conn.execute("""UPDATE ka.agent_sessions_cc SET status='working',status_message='',turns=%s,
                stop_requested_at=NULL,last_activity_at=now() WHERE id=%s""", (number, self.id))
            return number, '\n\n'.join(item['text'] for item in inputs)

    async def run_turn(self, number: int, text: str) -> None:
        recorder = RunRecorder(run_key=f'cc:{self.id}:{number}', project_id=str(self.session['project_id']),
                               agent_id=self.session['agent_id'], actor_id=str(self.session['actor_id']), secrets=self.secrets)
        recorder.add('start', {'kind': 'claude_code', 'title': self.session['title'], 'agent': self.session['agent_name'],
                               'turn': number})
        if number == 1:
            recorder.add('instructions', {'agent': self.session['agent_name'],
                                          'text': self.session['prompt_snapshot']['system_prompt']})
        recorder.add('input', {'task': text})
        await asyncio.to_thread(recorder.flush)
        turn = Turn(recorder)
        status, message, consumer = 'failed', '', None
        try:
            await self.connect()
            await self.client.query(text)
            consumer = asyncio.create_task(self.consume(turn))
            interrupted_at = None
            while not consumer.done():
                await asyncio.wait({consumer}, timeout=self.poll)
                if consumer.done():
                    break
                if interrupted_at is None:
                    if await asyncio.to_thread(self.stop_requested):
                        interrupted_at = time.monotonic()
                        await self.client.interrupt()
                elif time.monotonic() - interrupted_at > INTERRUPT_GRACE_SECONDS:
                    consumer.cancel()
                    with suppress(asyncio.CancelledError):
                        await consumer
                    await self.disconnect()
            if interrupted_at is not None:
                if not consumer.cancelled():
                    consumer.exception()  # retrieved: a stopped turn's error does not matter
                status, message = 'stopped', STOPPED
            else:
                consumer.result()  # raises the SDK error of this turn
                if turn.result is None or turn.result.is_error:
                    message = safe_error(result_error(turn.result), self.secrets)
                    recorder.add('error', {'message': message})
                else:
                    status = 'waiting'
        except Exception as exc:
            status = 'failed'
            message = safe_error(f"{type(exc).__name__}: {exc} {' '.join(self.stderr)}", self.secrets)
            recorder.add('error', {'type': type(exc).__name__, 'message': message})
            logger.warning('Claude Code session %s turn %s failed (%s)', self.id, number, type(exc).__name__)
            await self.disconnect()
        finally:
            if consumer is not None and not consumer.done():
                consumer.cancel()
        recorder.add('end', {'status': END_STATUS[status]})
        await asyncio.to_thread(recorder.flush)
        await asyncio.to_thread(self.finish_turn, number, status, message, turn)

    async def consume(self, turn: Turn) -> None:
        async for message in self.client.receive_response():
            turn.add(message)
            await asyncio.to_thread(turn.recorder.flush)

    async def connect(self) -> None:
        if self.client is not None:
            return
        model, session = await asyncio.to_thread(self.load)
        self.secrets[:] = [model.api_key] if model.api_key else []
        missing = next((f['path'] for f in session['context_snapshot']['folders'] if not Path(f['path']).is_dir()), None)
        if missing is not None:
            raise ValueError(f'The folder {missing} no longer exists. Start a new session without it.')
        options = build_options(session, model)
        options['stderr'] = self.stderr.append
        stack = AsyncExitStack()
        try:
            self.client = await stack.enter_async_context(self.client_factory(options))
        except BaseException:
            await stack.aclose()
            raise
        self.stack = stack

    def load(self) -> tuple[RuntimeModel, dict]:
        with self.store.connect() as conn:
            row = conn.execute('SELECT * FROM ka.llm_models WHERE id=%s', (self.session['model_id'],)).fetchone()
            session = conn.execute('''SELECT context_snapshot,prompt_snapshot,claude_session_id FROM ka.agent_sessions_cc
                WHERE id=%s''', (self.id,)).fetchone()
        if row is None or not row['enabled']:
            raise ValueError('The session model is disabled or was removed. Enable it in LLM Settings.')
        try:
            return decode_model(row, self.store.cipher), session
        except InvalidTag:
            raise ValueError('The model key could not be decrypted. Save it again in LLM Settings.') from None

    async def disconnect(self) -> None:
        stack, self.stack, self.client = self.stack, None, None
        if stack is not None:
            with suppress(Exception):
                await stack.aclose()

    def stop_requested(self) -> bool:
        with self.store.connect() as conn:
            row = conn.execute('SELECT status,stop_requested_at FROM ka.agent_sessions_cc WHERE id=%s', (self.id,)).fetchone()
        return row is None or row['status'] == 'closed' or row['stop_requested_at'] is not None

    def finish_turn(self, number: int, status: str, message: str, turn: Turn) -> None:
        usage = turn.usage or {'input_tokens': 0, 'output_tokens': 0, 'cached_tokens': 0, 'cost_usd': 0.0}
        draft = redact(turn.draft, self.secrets) if status == 'waiting' and turn.draft.strip() else None
        with self.store.connect() as conn:
            conn.execute("""UPDATE ka.agent_sessions_cc SET
                status=CASE WHEN status='closed' THEN status ELSE %(status)s END,
                status_message=CASE WHEN status='closed' THEN status_message ELSE %(message)s END,
                draft=coalesce(%(draft)s::text, draft),
                draft_turn=CASE WHEN %(draft)s::text IS NULL THEN draft_turn ELSE %(turn)s END,
                claude_session_id=coalesce(%(cli)s::text, claude_session_id),
                input_tokens=input_tokens+%(input)s, output_tokens=output_tokens+%(output)s,
                cached_tokens=cached_tokens+%(cached)s, cost_usd=cost_usd+%(cost)s::numeric,
                stop_requested_at=NULL, last_activity_at=now() WHERE id=%(id)s""",
                {'status': status, 'message': message, 'draft': draft, 'turn': number, 'cli': turn.session_id,
                 'input': usage['input_tokens'], 'output': usage['output_tokens'], 'cached': usage['cached_tokens'],
                 'cost': usage['cost_usd'], 'id': self.id})

    def release(self) -> None:
        with self.store.connect() as conn:
            conn.execute('UPDATE ka.agent_sessions_cc SET worker_id=NULL,lease_expires_at=NULL WHERE id=%s AND worker_id=%s',
                         (self.id, self.session['worker_id']))


async def serve(store: RepositoryStore, client_factory=sdk_client, *, stopped: asyncio.Event, worker_id: str | None = None,
                limit: int | None = None, poll: float = 1.0, idle_release: float = IDLE_RELEASE_SECONDS) -> None:
    """Claim sessions and run one task per session until `stopped` is set; leases are renewed every poll."""
    worker_id = worker_id or f'{socket.gethostname()}:{os.getpid()}'
    limit = limit or int(os.environ.get('CLAUDE_CODE_MAX_SESSIONS', '4'))
    active: dict[UUID, asyncio.Task] = {}
    try:
        while not stopped.is_set():
            for session_id, task in list(active.items()):
                if task.done():
                    del active[session_id]
                    if not task.cancelled() and task.exception() is not None:
                        logger.warning('Claude Code session %s ended with %s', session_id, type(task.exception()).__name__)
            try:
                claimed = await asyncio.to_thread(claim, store, worker_id, list(active), limit - len(active))
            except psycopg.Error as exc:
                logger.warning('Claude Code worker database unavailable (%s); retrying', type(exc).__name__)
                claimed = []
            for session in claimed:
                runner = SessionRunner(store, session, client_factory, poll=poll, idle_release=idle_release)
                active[session['id']] = asyncio.create_task(runner.run())
            with suppress(TimeoutError):
                await asyncio.wait_for(stopped.wait(), poll)
    finally:
        for task in active.values():
            task.cancel()
        await asyncio.gather(*active.values(), return_exceptions=True)


def secrets_from_environment() -> tuple[bytes, str]:
    """Take the encryption key and the database password out of os.environ: the SDK passes os.environ to the CLI."""
    key = base64.urlsafe_b64decode(os.environ.pop('CREDENTIAL_ENCRYPTION_KEY'))
    password = os.environ.pop('PGPASSWORD', None)
    return key, make_conninfo('', password=password) if password else ''


def main() -> None:
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s %(message)s')
    key, conninfo = secrets_from_environment()
    store = RepositoryStore(conninfo, key, os.environ['AUTH_ISSUER_URL'])
    run_record.configure(store, None)

    async def run() -> None:
        stopped = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(signum, stopped.set)
        await serve(store, stopped=stopped)

    asyncio.run(run())
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_claude_code_runner.py tests/test_claude_code_worker.py tests/test_claude_code_sessions.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/admin-api/src/knowledge_admin/claude_code_worker.py services/admin-api/src/knowledge_admin/claude_code_cli.py \
  services/admin-api/tests/claude_code_fakes.py services/admin-api/tests/test_claude_code_runner.py
git commit -m "feat(admin-api): Claude Code worker runs one client per session with queued input, stop and close

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Packaging — the SDK dependency, the image and the `claude-code-worker` service

**Files:**
- Modify: `services/admin-api/pyproject.toml`
- Modify: `services/admin-api/uv.lock` (generated by `uv lock`)
- Modify: `deploy/Dockerfile.admin-api`
- Modify: `deploy/compose.yaml`
- Modify: `tests/test_setup.py`
- Modify: `services/admin-api/tests/test_claude_code_worker.py`

**Interfaces:**
- Consumes: Task 7's `claude_code_worker.main` and `claude_code_cli.main`.
- Produces: scripts `knowledge-claude-code-worker` and `knowledge-claude-cli`; dependency `claude-agent-sdk==0.2.163`;
  user `claude` (uid 1001) with `/home/claude/tmp` in the image; compose service `claude-code-worker` and volume
  `claude-code-home`.

- [ ] **Step 1: Write the failing tests**

Append to `services/admin-api/tests/test_claude_code_worker.py`:

```python
def test_fakes_match_the_sdk_message_types():
    import dataclasses
    sdk = pytest.importorskip('claude_agent_sdk')
    import claude_code_fakes as fakes
    for name in ('TextBlock', 'ThinkingBlock', 'ToolUseBlock', 'ToolResultBlock', 'AssistantMessage', 'UserMessage',
                 'SystemMessage', 'ResultMessage'):
        real = {field.name for field in dataclasses.fields(getattr(sdk, name))}
        fake = {field.name for field in dataclasses.fields(getattr(fakes, name))}
        assert fake <= real, (name, fake - real)
    options = {field.name for field in dataclasses.fields(sdk.ClaudeAgentOptions)}
    assert set(worker.build_options(SESSION, BEDROCK)) | {'cli_path', 'stderr'} <= options
```

In `tests/test_setup.py`, add `"claude-code-worker": []` to the `writers` dict of
`test_compose_mounts_the_data_root_read_only_and_writer_folders_writable`, and add this method to `SetupTests`:

```python
    def test_claude_code_worker_mounts_and_environment(self) -> None:
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copy(source / ".env.example", root / ".env.example")
            shutil.copytree(source / "examples", root / "examples")
            run_setup(root, str(root / "data"))
            result = subprocess.run(
                ["docker", "compose", "--env-file", str(root / ".env"), "-f", str(source / "deploy/compose.yaml"), "config", "--format", "json"],
                capture_output=True, text=True, check=True,
            )
            config = json.loads(result.stdout)
            worker = config["services"]["claude-code-worker"]
            data = str(root / "data").replace("$", "$$")
            self.assertEqual(sorted((v["type"], v["source"], v["target"], bool(v.get("read_only"))) for v in worker["volumes"]),
                             [("bind", data, "/work/data", True), ("volume", "claude-code-home", "/home/claude", False)])
            self.assertTrue(worker["read_only"])
            self.assertEqual(worker["cap_drop"], ["ALL"])
            self.assertEqual(sorted(worker["cap_add"]), ["SETGID", "SETUID"])
            self.assertEqual(worker["entrypoint"], ["/services/admin-api/.venv/bin/knowledge-claude-code-worker"])
            self.assertNotIn("ports", worker)
            self.assertEqual(set(worker.get("networks", {"default": None})), {"default"})
            env = worker["environment"]
            self.assertEqual(env["CLAUDE_CODE_CLI"], "/services/admin-api/.venv/bin/knowledge-claude-cli")
            self.assertEqual((env["HOME"], env["TMPDIR"], env["DATA_ROOT_DIR"], env["CODE_ROOT"]),
                             ("/home/claude", "/home/claude/tmp", "/work/data", "/work/data/code"))
            for name in ("PGHOST", "PGPASSWORD", "CREDENTIAL_ENCRYPTION_KEY", "AUTH_ISSUER_URL"):
                self.assertIn(name, env)
            self.assertNotIn("AGENT_SHELL_URL", env)
            self.assertIn("claude-code-home", config["volumes"])
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `python3 -m unittest tests.test_setup -k claude_code_worker`
Expected: FAIL with `KeyError: 'claude-code-worker'`.

Run: `cd services/admin-api && uv run --frozen pytest tests/test_claude_code_worker.py::test_fakes_match_the_sdk_message_types -q`
Expected: SKIPPED (`could not import 'claude_agent_sdk'`).

- [ ] **Step 3: Add the dependency and scripts, then lock**

In `services/admin-api/pyproject.toml`, add `"claude-agent-sdk==0.2.163",` to `dependencies` (after
`"openinference-instrumentation-openai-agents==2.4.1",`) and to `[project.scripts]`:

```toml
knowledge-claude-code-worker = "knowledge_admin.claude_code_worker:main"
knowledge-claude-cli = "knowledge_admin.claude_code_cli:main"
```

Run: `cd services/admin-api && uv lock`
Expected: `Resolved … packages`, and `git diff --stat uv.lock` shows `claude-agent-sdk` 0.2.163 added (with its
manylinux and macOS wheels) and no other version changes. If `uv lock` reports a conflict (`mcp` is shared:
`claude-agent-sdk` needs `mcp>=1.23.0,<3.0.0`, admin-api pins `mcp>=1.26,<2`), stop and report it.

Run: `cd services/admin-api && uv run --frozen python -c "import importlib.metadata as m, claude_agent_sdk, pathlib; print(m.version('claude-agent-sdk'), (pathlib.Path(claude_agent_sdk.__file__).parent / '_bundled' / 'claude').exists())"`
Expected: `0.2.163 True`.

- [ ] **Step 4: Update the image**

In `deploy/Dockerfile.admin-api`, after `RUN mkdir -p /work/agent-workspaces`, add:

```dockerfile
# Claude Code runs as this unprivileged user (knowledge-claude-cli drops to it); /home/claude is the only writable
# mount of the claude-code-worker container. The CLI comes bundled in the claude-agent-sdk wheel (no Node.js).
RUN useradd --uid 1001 --user-group --home-dir /home/claude --create-home --shell /usr/sbin/nologin claude \
    && install -d -o claude -g claude -m 700 /home/claude/tmp \
    && chmod 700 /home/claude \
    && test -x "$(/services/admin-api/.venv/bin/python -c 'import claude_agent_sdk, pathlib; print(pathlib.Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude")')"
```

- [ ] **Step 5: Add the service**

In `deploy/compose.yaml`, after the `documentation-worker` service, add:

```yaml
  claude-code-worker:
    build: {context: .., dockerfile: deploy/Dockerfile.admin-api}
    restart: unless-stopped
    # Interactive documentation sessions with the Claude Agent SDK. DATA_ROOT is read-only and the CLI's home is the
    # only writable mount. It needs the network for Bedrock or Anthropic, so Bash can reach the network too (accepted).
    # It runs as root to hold its secrets; the CLI runs as the claude user (knowledge-claude-cli), hence SETUID/SETGID.
    entrypoint: [/services/admin-api/.venv/bin/knowledge-claude-code-worker]
    command: []
    read_only: true
    cap_drop: [ALL]
    cap_add: [SETUID, SETGID]
    security_opt: ["no-new-privileges:true"]
    dns_search: ${DOCKER_DNS_SEARCH:-.}
    volumes:
      - *data-root
      - claude-code-home:/home/claude
    environment:
      <<: *data-env
      HOME: /home/claude
      TMPDIR: /home/claude/tmp
      CLAUDE_CODE_CLI: /services/admin-api/.venv/bin/knowledge-claude-cli
      PGHOST: postgres
      PGDATABASE: knowledge_agent
      PGUSER: knowledge_agent
      PGPASSWORD: ${POSTGRES_PASSWORD:?Run make setup}
      CREDENTIAL_ENCRYPTION_KEY: ${CREDENTIAL_ENCRYPTION_KEY:?Run make setup}
      AUTH_ISSUER_URL: http://${WEB_HOST:-localhost}:${KEYCLOAK_PORT:-8181}/realms/docs
    depends_on:
      migrate: {condition: service_completed_successfully}
```

and add `claude-code-home:` to the top-level `volumes:` list.

- [ ] **Step 6: Run the tests and confirm they pass**

Run: `python3 -m unittest tests.test_setup`
Expected: PASS (this renders `deploy/compose.yaml` with a temporary `.env`; it never reads the repository `.env`).

Run: `cd services/admin-api && uv run --frozen pytest tests/test_claude_code_worker.py tests/test_claude_code_runner.py -q`
Expected: PASS, including `test_fakes_match_the_sdk_message_types`.

- [ ] **Step 7: Commit**

```bash
git add services/admin-api/pyproject.toml services/admin-api/uv.lock deploy/Dockerfile.admin-api deploy/compose.yaml \
  tests/test_setup.py services/admin-api/tests/test_claude_code_worker.py
git commit -m "feat(deploy): claude-code-worker service with the Claude Agent SDK and a read-only DATA_ROOT

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---
### Task 9: admin-ui — Claude Code agents in All agents, their page and the board

**Files:**
- Create: `services/admin-ui/app/lib/claudeCode.ts`
- Modify: `services/admin-ui/app/lib/profiles.ts` (`runtime` on `AgentProfile`)
- Create: `services/admin-ui/app/components/ClaudeCodeAgentForm.tsx`
- Modify: `services/admin-ui/app/components/AgentsView.tsx`
- Modify: `services/admin-ui/app/components/AgentDetailView.tsx`
- Modify: `services/admin-ui/app/components/AgentBoardView.tsx`
- Create: `services/admin-ui/app/components/__tests__/claudeCodeAgents.test.tsx`
- Modify: `services/admin-ui/app/components/__tests__/agentBoard.test.tsx`

**Interfaces:**
- Consumes: Task 2's agent routes and `runtime`; Tasks 3–5's session routes (typed here for Tasks 10–12).
- Produces, in `lib/claudeCode.ts`: `CLAUDE_CODE_SDK = 'Claude Code SDK'`, `CLAUDE_CODE_TOOLS = 'Read, Grep, Glob, Bash'`,
  `claudeModel(model)`, `formatTokens(n)`, `createClaudeCodeAgent`, `saveClaudeCodeAgent`, `SessionStatus`,
  `statusLabels`, `SessionFolder`, `SessionSummary`, `SessionDetail`, `SessionState`, `SessionEvent`, `SessionInput`,
  `createSession`, `getSessions`, `getSession`, `sessionEventsUrl`, `sendMessage`, `stopSession`, `closeSession`,
  `saveSessionDocument`; `ClaudeCodeAgentForm({ agent?, onSaved, onCancel? })`.

- [ ] **Step 1: Write the failing tests**

Create `services/admin-ui/app/components/__tests__/claudeCodeAgents.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useParams } from 'react-router';
import { afterEach, expect, test, vi } from 'vitest';
vi.mock('../ReaderAssistantCard', () => ({ default: () => null }));
import AgentsView from '../AgentsView';
import AgentDetailView from '../AgentDetailView';

const base = { when_to_use: '', enabled: true, is_coordinator: false, documents_enabled: false, repositories_enabled: false,
  ssh_enabled: false, skills_enabled: false, mcp_enabled: false, services_enabled: false, documents_grounded: false, revision: 1,
  all_collections: false, all_repositories: false, collection_ids: [], repository_ids: [], service_ids: [], skill_ids: [], active_prompt: null };
const writer = { ...base, id: 'writer', name: 'Technical writer', description: 'Writes documentation', runtime: 'claude_code',
  model_id: null, model_name: null, prompt_title: null, prompt_version: null };
const search = { ...base, id: 'search', name: 'Search agent', description: '', runtime: 'openai', documents_enabled: true,
  model_id: 'm', model_name: 'GPT', prompt_title: 'Search', prompt_version: 2 };
afterEach(() => vi.unstubAllGlobals());

function setup() {
  const calls: { url: string; method: string; body?: unknown }[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    calls.push({ url, method, body: init?.body ? JSON.parse(init.body as string) : undefined });
    if (url === '/api/agents') return Response.json({ agents: [search, writer] });
    if (url === '/api/claude-code/agents' && method === 'POST') return Response.json({ id: 'new-writer' }, { status: 201 });
    if (url === '/api/claude-code/agents/writer' && method === 'PUT') return Response.json({ ...writer, name: 'Writer', revision: 2 });
    if (url === '/api/agents/new-writer') return Response.json({ ...writer, id: 'new-writer', name: 'Release notes writer', description: 'Writes release notes' });
    if (url.startsWith('/api/agents/')) return Response.json({ ...writer, id: url.split('/').pop() });
    return Response.json({});
  }));
  return calls;
}
function Detail() { const { id } = useParams(); return <AgentDetailView agentId={id!} />; }
function app(path: string) {
  return <MemoryRouter initialEntries={[path]}><Routes><Route path="/agents" element={<AgentsView />} /><Route path="/agents/:id" element={<Detail />} /></Routes></MemoryRouter>;
}

test('All agents shows Claude Code agents with their badge and creates one from a name and description', async () => {
  const calls = setup();
  render(app('/agents'));
  const row = (await screen.findByRole('link', { name: 'Technical writer' })).closest('tr')!;
  expect(within(row).getByText('Claude Code SDK')).toBeVisible();
  expect(within(row).getByText('Read, Grep, Glob, Bash')).toBeVisible();
  expect(within(row).getByText('Chosen per session')).toBeVisible();
  expect(within(row).getByText('Built in')).toBeVisible();
  const other = screen.getByRole('link', { name: 'Search agent' }).closest('tr')!;
  expect(within(other).queryByText('Claude Code SDK')).toBeNull();
  expect(screen.getByRole('button', { name: 'New Agent Wizard' })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'New Claude Code agent' }));
  expect(screen.queryByLabelText(/Model/)).toBeNull();
  expect(screen.queryByLabelText(/Instructions/)).toBeNull();
  expect(screen.getByRole('button', { name: 'Create agent' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Release notes writer' } });
  fireEvent.change(screen.getByLabelText('Description'), { target: { value: 'Writes release notes' } });
  fireEvent.click(screen.getByRole('button', { name: 'Create agent' }));
  await waitFor(() => expect(calls.find(c => c.method === 'POST')).toEqual({
    url: '/api/claude-code/agents', method: 'POST', body: { name: 'Release notes writer', description: 'Writes release notes' } }));
  expect(await screen.findByRole('heading', { name: 'Release notes writer', level: 2 })).toBeVisible();
});

test('a Claude Code agent page edits only its name and description', async () => {
  const calls = setup();
  render(app('/agents/writer'));
  expect(await screen.findByRole('heading', { name: 'Technical writer', level: 2 })).toBeVisible();
  expect(screen.getByText('Claude Code SDK')).toBeVisible();
  expect(screen.queryByRole('tab')).toBeNull();
  expect(screen.queryByRole('combobox', { name: 'Model connection' })).toBeNull();
  expect(screen.getByText(/are chosen when a session starts/)).toBeVisible();
  expect(screen.getByRole('link', { name: /With an agent/ })).toHaveAttribute('href', '/documentation/generations/new?scope=agent');
  fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Writer' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save agent' }));
  await waitFor(() => expect(calls.find(c => c.method === 'PUT')).toEqual({
    url: '/api/claude-code/agents/writer', method: 'PUT', body: { name: 'Writer', description: 'Writes documentation', revision: 1 } }));
  expect(await screen.findByRole('heading', { name: 'Writer', level: 2 })).toBeVisible();
});
```

In `services/admin-ui/app/components/__tests__/agentBoard.test.tsx`, add to the `agents` array:

```tsx
  { id: 'writer', name: 'Technical writer', runtime: 'claude_code', enabled: true, model_id: null, prompt_version: null },
```

and in the first test, after `expect(screen.getByRole('checkbox', { name: 'Disabled agent' })).toBeDisabled();`, add:

```tsx
  expect(screen.queryByRole('checkbox', { name: 'Technical writer' })).toBeNull();
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-ui && pnpm exec vitest run app/components/__tests__/claudeCodeAgents.test.tsx app/components/__tests__/agentBoard.test.tsx`
Expected: FAIL: `Unable to find an element with the text: Claude Code SDK` and the board shows the `Technical writer`
checkbox.

- [ ] **Step 3: Create `services/admin-ui/app/lib/claudeCode.ts`**

```ts
import { request } from './api';
import type { ModelProfile } from './llm';

export const CLAUDE_CODE_SDK = 'Claude Code SDK';
export const CLAUDE_CODE_TOOLS = 'Read, Grep, Glob, Bash';
/** The server's Claude connection rule (knowledge_llm.claude_model). */
export const claudeModel = (model: Pick<ModelProfile, 'provider' | 'api_format' | 'model'>) =>
  model.api_format === 'native' && (model.provider === 'anthropic' || model.provider === 'bedrock' && model.model.includes('anthropic.claude'));
export const formatTokens = (value: number) => value.toLocaleString('en-US');
const json = (method: string, value: unknown) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) });

export interface ClaudeCodeAgentInput { name: string; description: string; revision?: number }
export interface ClaudeCodeAgent { id: string; name: string; description: string; revision: number }
export const createClaudeCodeAgent = (value: ClaudeCodeAgentInput) => request<{ id: string }>('/api/claude-code/agents', json('POST', value));
export const saveClaudeCodeAgent = (id: string, value: ClaudeCodeAgentInput) =>
  request<ClaudeCodeAgent>(`/api/claude-code/agents/${encodeURIComponent(id)}`, json('PUT', value));

export type SessionStatus = 'starting' | 'working' | 'waiting' | 'stopped' | 'failed' | 'closed';
export const statusLabels: Record<SessionStatus, string> = {
  starting: 'Starting', working: 'Working', waiting: 'Waiting for you', stopped: 'Stopped', failed: 'Failed', closed: 'Finished',
};
export interface SessionFolder { path: string; kind: 'code_project' | 'folder'; name: string; code_project_id?: string }
export interface SessionSummary {
  id: string; title: string; agent_id: string; agent_name: string; model_name: string; status: SessionStatus; status_message: string;
  turns: number; input_tokens: number; output_tokens: number; cached_tokens: number; cost_usd: number;
  document_id: string | null; document_title: string | null; folder: string; worker_active: boolean; queued: number;
  created_at: string; last_activity_at: string; closed_at: string | null;
}
export interface SessionDetail extends SessionSummary {
  document_name: string; draft: string | null; draft_turn: number | null; model_id: string;
  prompt_snapshot: { id: string; name: string; version: number; comments: string; system_prompt: string };
  context_snapshot: { folders: SessionFolder[]; code_project_ids: string[]; servers: { id: string; name: string; hostname: string; description: string }[]; notes: string };
  inputs: { seq: number; text: string; status: 'queued' | 'delivered' | 'discarded'; turn: number | null; created_at: string }[];
}
export type SessionState = Pick<SessionDetail, 'status' | 'status_message' | 'turns' | 'input_tokens' | 'output_tokens' | 'cached_tokens'
  | 'cost_usd' | 'draft' | 'draft_turn' | 'document_id' | 'worker_active' | 'queued'>;
export interface SessionEvent { id: number; turn: number; seq: number; kind: string; payload: Record<string, unknown>; created_at: string }
export interface SessionInput {
  agent_id: string; model_id: string; prompt_id: string; prompt_version_id: string; comments: string;
  code_project_ids: string[]; folders: string[]; server_ids: string[]; notes: string; document_name: string; folder: string;
}
export interface SavedDocument { id: string; revision: number; version_id: string; folder: string; output_folder: string | null; export_error: string | null }

const session = (id: string) => `/api/claude-code/sessions/${encodeURIComponent(id)}`;
export const createSession = (value: SessionInput) => request<SessionDetail>('/api/claude-code/sessions', json('POST', value));
export const getSessions = (offset = 0, signal?: AbortSignal) => request<{ sessions: SessionSummary[] }>(`/api/claude-code/sessions?offset=${offset}`, { signal });
export const getSession = (id: string, signal?: AbortSignal) => request<SessionDetail>(session(id), { signal });
export const sessionEventsUrl = (id: string) => `${session(id)}/events`;
export const sendMessage = (id: string, text: string) => request<{ seq: number; status: 'queued'; working: boolean }>(`${session(id)}/messages`, json('POST', { text }));
export const stopSession = (id: string) => request<{ id: string; discarded: number }>(`${session(id)}/stop`, { method: 'POST' });
export const closeSession = (id: string) => request<{ id: string }>(`${session(id)}/close`, { method: 'POST' });
export const saveSessionDocument = (id: string, updateExisting: boolean) =>
  request<SavedDocument>(`${session(id)}/document`, json('POST', { update_existing: updateExisting }));
```

In `services/admin-ui/app/lib/profiles.ts`, add `runtime?: 'openai' | 'claude_code';` to `AgentProfile` (after
`is_coordinator: boolean;`).

- [ ] **Step 4: Create `services/admin-ui/app/components/ClaudeCodeAgentForm.tsx`**

```tsx
import { useState } from 'react';
import { ApiError } from '~/lib/api';
import { createClaudeCodeAgent, saveClaudeCodeAgent, type ClaudeCodeAgent } from '~/lib/claudeCode';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Button } from '~/components/ui/button';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '~/components/ui/card';
import { Field, FieldDescription, FieldGroup, FieldLabel } from '~/components/ui/field';
import { Input } from '~/components/ui/input';
import { Textarea } from '~/components/ui/textarea';

/** A Claude Code agent is a registration: a name and a description. Model, folders and prompt come with each session. */
export default function ClaudeCodeAgentForm({ agent, onSaved, onCancel }: {
  agent?: ClaudeCodeAgent; onSaved: (saved: ClaudeCodeAgent) => void; onCancel?: () => void;
}) {
  const [name, setName] = useState(agent?.name ?? '');
  const [description, setDescription] = useState(agent?.description ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function submit() {
    setBusy(true); setError('');
    try {
      const value = { name: name.trim(), description: description.trim() };
      if (agent) onSaved(await saveClaudeCodeAgent(agent.id, { ...value, revision: agent.revision }));
      else onSaved({ id: (await createClaudeCodeAgent(value)).id, ...value, revision: 1 });
    } catch (e) { setError(e instanceof ApiError ? e.message : 'Could not save the agent.'); }
    finally { setBusy(false); }
  }
  return <Card>
    <CardHeader><CardTitle>{agent ? 'Claude Code agent' : 'New Claude Code agent'}</CardTitle>
      <CardDescription>Runs with the Claude Agent SDK in its own container: read-only file tools and Bash. The model, the folders and the prompt are chosen when a session starts.</CardDescription></CardHeader>
    <CardContent><form id="claude-code-agent" onSubmit={e => { e.preventDefault(); void submit(); }}><FieldGroup>
      <Field><FieldLabel htmlFor="claude-code-agent-name">Name</FieldLabel><Input id="claude-code-agent-name" maxLength={100} value={name} onChange={e => setName(e.target.value)} /></Field>
      <Field><FieldLabel htmlFor="claude-code-agent-description">Description</FieldLabel><Textarea id="claude-code-agent-description" maxLength={4000} value={description} onChange={e => setDescription(e.target.value)} />
        <FieldDescription>Optional. Shown when you choose an agent for a session.</FieldDescription></Field>
      {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    </FieldGroup></form></CardContent>
    <CardFooter className="gap-2"><Button type="submit" form="claude-code-agent" disabled={busy || !name.trim()}>{agent ? 'Save agent' : 'Create agent'}</Button>
      {onCancel && <Button type="button" variant="outline" disabled={busy} onClick={onCancel}>Cancel</Button>}</CardFooter>
  </Card>;
}
```

- [ ] **Step 5: Show the runtime in All agents, the agent page and the board**

In `services/admin-ui/app/components/AgentsView.tsx`:
- import `ClaudeCodeAgentForm` and `{ CLAUDE_CODE_SDK, CLAUDE_CODE_TOOLS } from '~/lib/claudeCode'`; add state
  `const [creating, setCreating] = useState(false);`;
- in the toolbar, after the **New Agent Wizard** button, add
  `<Button variant="outline" onClick={() => setCreating(true)} disabled={creating}>New Claude Code agent</Button>`, and
  below `{wizard && …}` add
  `{creating && <ClaudeCodeAgentForm onCancel={() => setCreating(false)} onSaved={saved => navigate(`/agents/${saved.id}`)} />}`;
- in the filter text add `${a.runtime === 'claude_code' ? 'claude code' : ''}`;
- in each row, compute `const claudeCode = a.runtime === 'claude_code';` (turn the `filtered?.map(a => <TableRow …>)`
  arrow into a block that returns the row) and change the cells to:

```tsx
<TableCell className="max-w-sm whitespace-normal"><Link className="font-medium underline-offset-4 hover:underline" to={`/agents/${a.id}`}>{a.name}</Link>{claudeCode && <Badge variant="secondary" className="ml-2">{CLAUDE_CODE_SDK}</Badge>}{a.config_version && <Badge variant="outline" className="ml-2">Agent v{a.config_version}</Badge>}{a.is_coordinator && <Badge className="ml-2">Coordinator</Badge>}{a.description && <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">{a.description}</p>}</TableCell>
<TableCell className="max-w-xs whitespace-normal">{claudeCode ? <span>{CLAUDE_CODE_TOOLS}</span> : /* the existing capability badges div, unchanged */}</TableCell>
<TableCell>{claudeCode ? 'Chosen per session' : a.model_name ?? 'Not configured'}</TableCell>
<TableCell>{claudeCode ? 'Built in' : a.prompt_version ? `v${a.prompt_version} · ${a.prompt_title}` : 'Not configured'}</TableCell>
```

  (the status and actions cells stay as they are).

In `services/admin-ui/app/components/AgentDetailView.tsx`, import `ClaudeCodeAgentForm` and `CLAUDE_CODE_SDK`, and
replace `{!profile ? <Skeleton className="h-64 w-full" /> : <>` … `</>}` with a three-way branch whose middle branch is:

```tsx
profile.runtime === 'claude_code' ? <>
  <div className="flex flex-wrap items-center gap-3"><h2 className="text-xl font-semibold">{profile.name}</h2><Badge variant="secondary">{CLAUDE_CODE_SDK}</Badge></div>
  <ClaudeCodeAgentForm key={profile.revision} agent={profile} onSaved={saved => setProfile({ ...profile, ...saved })} />
  <p className="text-sm text-muted-foreground">Start a session in <Link className="underline" to="/documentation/generations/new?scope=agent">Docs → Generate documents → New documentation → With an agent</Link>.</p>
</>
```

(the OpenAI branch with its tabs is unchanged).

In `services/admin-ui/app/components/AgentBoardView.tsx`, change the agent filter to
`data.agents.filter(agent => !agent.is_coordinator && agent.runtime !== 'claude_code')`.

- [ ] **Step 6: Run the tests and confirm they pass**

Run: `cd services/admin-ui && pnpm exec tsc --noEmit && pnpm exec vitest run app/components/__tests__/claudeCodeAgents.test.tsx app/components/__tests__/agentBoard.test.tsx app/components/__tests__/agentTabs.test.tsx app/components/__tests__/agentWizard.test.tsx`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add services/admin-ui/app/lib/claudeCode.ts services/admin-ui/app/lib/profiles.ts \
  services/admin-ui/app/components/ClaudeCodeAgentForm.tsx services/admin-ui/app/components/AgentsView.tsx \
  services/admin-ui/app/components/AgentDetailView.tsx services/admin-ui/app/components/AgentBoardView.tsx \
  services/admin-ui/app/components/__tests__/claudeCodeAgents.test.tsx services/admin-ui/app/components/__tests__/agentBoard.test.tsx
git commit -m "feat(admin-ui): Claude Code agents with their badge, a name-and-description page, off the board

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: admin-ui — New documentation → With an agent

**Files:**
- Create: `services/admin-ui/app/components/ClaudeCodeSessionForm.tsx`
- Modify: `services/admin-ui/app/components/DocumentationWorkflowView.tsx` (`NewDocumentationWorkflow`)
- Create: `services/admin-ui/app/components/__tests__/claudeCodeSessionForm.test.tsx`

**Interfaces:**
- Consumes: Task 9's `lib/claudeCode.ts`; `getAgents`, `getLlmSettings`, `getDocumentationPrompts`, `getCodeProjects`,
  `getServers`, `getDocumentationFolders`, `defaultFolder`, `documentSlug`, `folderError`, `PROMPT_CHANGED`,
  `FolderBrowser` and `normalizeFolder`.
- Produces: `ClaudeCodeSessionForm({ onCreated(id) })`; scope `agent` (**With an agent**) in `NewDocumentationWorkflow`,
  preselected by `?scope=agent`; after **Generate** the page opens `/documentation/sessions/<id>`.

- [ ] **Step 1: Write the failing tests**

Create `services/admin-ui/app/components/__tests__/claudeCodeSessionForm.test.tsx`:

```tsx
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, expect, test, vi } from 'vitest';
import { NewDocumentationWorkflow } from '../DocumentationWorkflowView';

afterEach(() => vi.unstubAllGlobals());
const bodies: Record<string, unknown> = {
  '/api/services': { services: [] }, '/api/environments': { environments: [] },
  '/api/llm': { models: [
    { id: 'claude', name: 'Claude on Bedrock', provider: 'bedrock', model: 'eu.anthropic.claude-sonnet-4-5-20250929-v1:0', api_format: 'native', enabled: true },
    { id: 'gpt', name: 'GPT', provider: 'openai', model: 'gpt-5', api_format: 'responses', enabled: true }],
    assignments: { search_model_id: null, code_model_id: null, revision: 1 } },
  '/api/agents': { agents: [{ id: 'writer', name: 'Technical writer', runtime: 'claude_code', enabled: true },
    { id: 'search', name: 'Search agent', runtime: 'openai', enabled: true }] },
  '/api/documentation/prompts': { prompts: [
    { id: 'code', name: 'Code analysis and architecture', input_kind: 'code', enabled: true, active_version_id: 'code-v1' },
    { id: 'ops', name: 'Deployment and service operations', input_kind: 'server', enabled: true, active_version_id: 'ops-v1' }] },
  '/api/code-projects': { projects: [{ id: 'repo', name: 'Payments API' }] },
  '/api/servers': { servers: [{ id: 'srv', name: 'prod-1', hostname: '10.0.0.5', description: 'Payments host' }] },
  '/api/documentation/folders': { generated_folder: 'generated-documents', folders: [] },
};

function setup(error?: string) {
  const posts: unknown[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/claude-code/sessions' && init?.method === 'POST') {
      posts.push(JSON.parse(init.body as string));
      return error ? Response.json({ error: { message: error } }, { status: 400 }) : Response.json({ id: 'session-1' }, { status: 201 });
    }
    if (url.startsWith('/api/rag/folders')) return Response.json({ path: null, folders: [{ name: 'jira', path: 'jira' }], truncated: false });
    return Response.json(bodies[url] ?? {});
  }));
  render(<MemoryRouter initialEntries={['/documentation/generations/new?scope=agent']}><Routes>
    <Route path="/documentation/generations/new" element={<NewDocumentationWorkflow />} />
    <Route path="/documentation/sessions/:sessionId" element={<div>Session page</div>} />
  </Routes></MemoryRouter>);
  return posts;
}
async function select(label: string, option: string) {
  fireEvent.click(await screen.findByLabelText(label));
  fireEvent.click(await screen.findByRole('option', { name: option }));
}
async function fill() {
  await select('Agent', 'Technical writer');
  fireEvent.click(screen.getByLabelText('Model'));
  expect(screen.queryByRole('option', { name: 'GPT' })).toBeNull();  // Claude connections only
  fireEvent.click(screen.getByRole('option', { name: 'Claude on Bedrock' }));
  await select('Prompt', 'Deployment and service operations');  // any active documentation prompt
  fireEvent.change(screen.getByLabelText('Document name'), { target: { value: 'Payments – API reference' } });
}

test('With an agent starts a session with the model, prompt and context and opens it', async () => {
  const posts = setup();
  await fill();
  expect(screen.queryByRole('button', { name: 'Create workflow' })).toBeNull();
  expect(screen.getByLabelText('Documentation folder')).toHaveValue('docs');
  expect(screen.getByRole('button', { name: 'Generate' })).toBeDisabled();  // no context yet
  fireEvent.click(screen.getByRole('checkbox', { name: 'Payments API' }));
  expect(screen.getByLabelText('Documentation folder')).toHaveValue('payments-api-docs');
  fireEvent.click(screen.getByRole('button', { name: 'Choose a folder' }));
  fireEvent.click(await screen.findByRole('button', { name: 'jira' }));
  fireEvent.click(screen.getByRole('button', { name: 'Add folder' }));
  expect(screen.getByRole('button', { name: 'Remove jira' })).toBeVisible();
  fireEvent.click(screen.getByRole('checkbox', { name: 'prod-1 (10.0.0.5)' }));
  fireEvent.change(screen.getByLabelText('Notes'), { target: { value: 'Deployed with Helm.' } });
  fireEvent.change(screen.getByLabelText('Comments'), { target: { value: 'Focus on the public API.' } });
  fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
  expect(await screen.findByText('Session page')).toBeVisible();
  expect(posts).toEqual([{ agent_id: 'writer', model_id: 'claude', prompt_id: 'ops', prompt_version_id: 'ops-v1',
    comments: 'Focus on the public API.', code_project_ids: ['repo'], folders: ['jira'], server_ids: ['srv'],
    notes: 'Deployed with Helm.', document_name: 'Payments – API reference', folder: 'payments-api-docs' }]);
});

test('a bad folder name blocks Generate and server refusals are shown', async () => {
  setup('The folder /work/data/jira does not exist. Remove it from the context.');
  await fill();
  fireEvent.click(screen.getByRole('checkbox', { name: 'Payments API' }));
  fireEvent.change(screen.getByLabelText('Documentation folder'), { target: { value: 'Payments' } });
  expect(screen.getByRole('button', { name: 'Generate' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Documentation folder'), { target: { value: 'payments' } });
  fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
  expect(await screen.findByText('The folder /work/data/jira does not exist. Remove it from the context.')).toBeVisible();
});
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-ui && pnpm exec vitest run app/components/__tests__/claudeCodeSessionForm.test.tsx`
Expected: FAIL: `Unable to find a label with the text of: Agent`.

- [ ] **Step 3: Create `services/admin-ui/app/components/ClaudeCodeSessionForm.tsx`**

```tsx
import { useEffect, useState } from 'react';
import { ApiError, getServers, type ServerAsset } from '~/lib/api';
import { claudeModel, createSession } from '~/lib/claudeCode';
import { getCodeProjects, type CodeProject } from '~/lib/codeProjects';
import { defaultFolder, documentSlug, folderError, getDocumentationFolders, getDocumentationPrompts, PROMPT_CHANGED,
  type DocumentationPrompt } from '~/lib/documents';
import { getLlmSettings, type ModelProfile } from '~/lib/llm';
import { getAgents, type AgentSummary } from '~/lib/profiles';
import { normalizeFolder } from '~/lib/rag';
import { FolderBrowser } from './RagFolderPicker';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Button } from '~/components/ui/button';
import { Checkbox } from '~/components/ui/checkbox';
import { Field, FieldContent, FieldDescription, FieldLabel, FieldLegend, FieldSet } from '~/components/ui/field';
import { Input } from '~/components/ui/input';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '~/components/ui/select';
import { Skeleton } from '~/components/ui/skeleton';
import { Textarea } from '~/components/ui/textarea';

type Data = { agents: AgentSummary[]; models: ModelProfile[]; prompts: DocumentationPrompt[]; projects: CodeProject[]; servers: ServerAsset[]; folders: string[] };

/** An interactive session: a Claude Code agent, a Claude model, a prompt, context and the document to write. */
export default function ClaudeCodeSessionForm({ onCreated }: { onCreated: (id: string) => void }) {
  const [data, setData] = useState<Data | null>(null);
  const [reload, setReload] = useState(0);
  const [agentId, setAgentId] = useState('');
  const [modelId, setModelId] = useState('');
  const [promptId, setPromptId] = useState('');
  const [projectIds, setProjectIds] = useState<string[]>([]);
  const [folders, setFolders] = useState<string[]>([]);
  const [choosing, setChoosing] = useState(false);
  const [folderValue, setFolderValue] = useState('');
  const [serverIds, setServerIds] = useState<string[]>([]);
  const [notes, setNotes] = useState('');
  const [comments, setComments] = useState('');
  const [name, setName] = useState('');
  const [folder, setFolder] = useState('docs');
  const [folderTouched, setFolderTouched] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    // The documentation folder list only feeds suggestions: a failing request must not block the form.
    Promise.all([getAgents(), getLlmSettings(), getDocumentationPrompts(), getCodeProjects(), getServers(),
      getDocumentationFolders().catch(() => ({ folders: [] as { folder: string }[] }))]).then(([agents, llm, prompts, projects, servers, docFolders]) => {
      if (cancelled) return;
      const models = llm.models.filter(model => model.enabled && claudeModel(model));
      setData({ agents: agents.agents.filter(agent => agent.runtime === 'claude_code' && agent.enabled), models,
        prompts: prompts.prompts.filter(prompt => prompt.enabled && prompt.active_version_id), projects: projects.projects,
        servers: servers.servers, folders: docFolders.folders.map(item => item.folder) });
      if (models.length === 1) setModelId(models[0].id);
    }).catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : 'Could not load agents, models and context'); });
    return () => { cancelled = true; };
  }, [reload]);

  function toggleProject(id: string, checked: boolean) {
    const next = checked ? [...projectIds, id] : projectIds.filter(item => item !== id);
    setProjectIds(next);
    const first = data?.projects.find(project => project.id === next[0]);
    if (!folderTouched) setFolder(first ? defaultFolder(first.name) : 'docs');
  }
  const toggleServer = (id: string, checked: boolean) => setServerIds(current => checked ? [...current, id] : current.filter(item => item !== id));
  const prompt = data?.prompts.find(item => item.id === promptId);
  const target = folder.trim();
  const badFolder = target ? folderError(target) : '';
  const picked = normalizeFolder(folderValue);
  const ready = Boolean(agentId && modelId && prompt?.active_version_id && (projectIds.length || folders.length)
    && documentSlug(name) && target && !badFolder);

  async function submit() {
    if (!ready || pending || !prompt?.active_version_id) return;
    setPending(true); setError('');
    try {
      const session = await createSession({ agent_id: agentId, model_id: modelId, prompt_id: prompt.id, prompt_version_id: prompt.active_version_id,
        comments, code_project_ids: projectIds, folders, server_ids: serverIds, notes, document_name: name.trim(), folder: target });
      onCreated(session.id);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not start the session');
      setPending(false);
    }
  }
  function choose(label: string, id: string, current: string, items: { id: string; name: string }[], change: (id: string) => void) {
    return <Field><FieldLabel htmlFor={id}>{label}</FieldLabel><Select value={current || 'none'} onValueChange={value => change(value === 'none' ? '' : value)}>
      <SelectTrigger id={id}><SelectValue /></SelectTrigger><SelectContent><SelectGroup><SelectItem value="none">Select…</SelectItem>
        {items.map(item => <SelectItem key={item.id} value={item.id}>{item.name}</SelectItem>)}</SelectGroup></SelectContent></Select></Field>;
  }
  function check(id: string, label: string, checked: boolean, change: (checked: boolean) => void) {
    return <Field key={id} orientation="horizontal"><Checkbox id={id} checked={checked} onCheckedChange={value => change(value === true)} />
      <FieldContent><FieldLabel htmlFor={id}>{label}</FieldLabel></FieldContent></Field>;
  }
  if (!data) return error ? <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert> : <Skeleton className="h-48 w-full" />;
  return <form className="flex flex-col gap-5" onSubmit={e => { e.preventDefault(); void submit(); }}>
    <fieldset disabled={pending} className="flex min-w-0 flex-col gap-5">
      {choose('Agent', 'session-agent', agentId, data.agents, setAgentId)}
      {data.agents.length === 0 && <FieldDescription>Create a Claude Code agent in Agents → New Claude Code agent.</FieldDescription>}
      {choose('Model', 'session-model', modelId, data.models, setModelId)}
      <FieldDescription>Claude connections only: Bedrock inference profiles (eu.anthropic.claude-…) or the Anthropic API, from LLM Settings.</FieldDescription>
      {choose('Prompt', 'session-prompt', promptId, data.prompts, setPromptId)}
      <FieldSet><FieldLegend>Context</FieldLegend>
        <FieldDescription>Code project folders and folders under DATA_ROOT become the agent's read-only working folders; the first one is its working directory. Servers and notes are passed as text: the agent has no server access.</FieldDescription>
        <p className="text-sm font-medium">Code projects</p>
        {data.projects.map(project => check(`session-project-${project.id}`, project.name, projectIds.includes(project.id), checked => toggleProject(project.id, checked)))}
        <p className="text-sm font-medium">Folders under DATA_ROOT</p>
        {folders.length > 0 && <ul className="flex flex-col gap-1">{folders.map(item => <li key={item} className="flex items-center gap-2 text-sm"><code className="break-all">{item}</code>
          <Button type="button" variant="ghost" size="sm" aria-label={`Remove ${item}`} onClick={() => setFolders(current => current.filter(f => f !== item))}>Remove</Button></li>)}</ul>}
        {choosing ? <>
          <FolderBrowser value={folderValue} onChange={setFolderValue} />
          <Button type="button" variant="outline" className="self-start" disabled={!picked || folders.includes(picked)}
            onClick={() => { setFolders(current => [...current, picked]); setFolderValue(''); setChoosing(false); }}>Add folder</Button>
        </> : <Button type="button" variant="outline" className="self-start" onClick={() => setChoosing(true)}>Choose a folder</Button>}
        <p className="text-sm font-medium">Servers</p>
        {data.servers.map(server => check(`session-server-${server.id}`, `${server.name} (${server.hostname})`, serverIds.includes(server.id), checked => toggleServer(server.id, checked)))}
        <Field><FieldLabel htmlFor="session-notes">Notes</FieldLabel><Textarea id="session-notes" rows={3} maxLength={20000} value={notes} onChange={e => setNotes(e.target.value)} /></Field>
      </FieldSet>
      <Field><FieldLabel htmlFor="session-comments">Comments</FieldLabel><Textarea id="session-comments" rows={3} maxLength={20000} value={comments} onChange={e => setComments(e.target.value)} placeholder="Extra instructions for this run" /></Field>
      <Field><FieldLabel htmlFor="session-document-name">Document name</FieldLabel><Input id="session-document-name" maxLength={240} value={name} onChange={e => setName(e.target.value)} placeholder="Payments – API reference" />
        {name && !documentSlug(name) && <FieldDescription className="text-destructive">Use letters or digits in the name.</FieldDescription>}</Field>
      <Field><FieldLabel htmlFor="session-folder">Documentation folder</FieldLabel><Input id="session-folder" list="session-documentation-folders" maxLength={200} value={folder} onChange={e => { setFolderTouched(true); setFolder(e.target.value); }} />
        <datalist id="session-documentation-folders">{data.folders.map(item => <option key={item} value={item} />)}</datalist>
        <FieldDescription className={badFolder ? 'text-destructive' : undefined}>{badFolder || `Save document stores it as ${target || '<folder>'}/${documentSlug(name) || '<document>'}/README.md in the generated documents folder.`}</FieldDescription></Field>
    </fieldset>
    {error && <Alert variant="destructive"><AlertDescription>{error}{error === PROMPT_CHANGED && <Button type="button" variant="link" onClick={() => setReload(n => n + 1)}>Reload prompts</Button>}</AlertDescription></Alert>}
    <Button type="submit" className="self-start" disabled={pending || !ready}>{pending ? 'Starting…' : 'Generate'}</Button>
  </form>;
}
```

- [ ] **Step 4: Add the scope to `NewDocumentationWorkflow`**

In `services/admin-ui/app/components/DocumentationWorkflowView.tsx`:
- import `useSearchParams` from `react-router` and `ClaudeCodeSessionForm`;
- `const [params] = useSearchParams();` and
  `const [scope,setScope]=useState<'service'|'environment'|'code_project'|'agent'>(params.get('scope')==='agent'?'agent':'service');`;
- the form's submit handler returns early for `'agent'` as for `'code_project'`;
- the intro paragraph shows, for `'agent'`: `An agent explores the folders and context you choose and writes a draft. Follow
  it live, send it messages, then save the draft as a document.`;
- the Scope choices become `[{id:'service',name:'Service'},{id:'environment',name:'Whole environment'},{id:'code_project',name:'Code project'},{id:'agent',name:'With an agent'}]`;
- the Service/Environment select and **Create workflow** render only when `scope` is `service` or `environment`; the
  workflow **Model** select renders only when `scope !== 'agent'` (the session form has its own required Model);
- after the existing `{scope==='code_project' && data && …}`, add
  `{scope==='agent' && data && <ClaudeCodeSessionForm onCreated={id => navigate(`/documentation/sessions/${id}`)} />}`.

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `cd services/admin-ui && pnpm exec tsc --noEmit && pnpm exec vitest run app/components/__tests__/claudeCodeSessionForm.test.tsx app/components/__tests__/codeProjectDocumentation.test.tsx app/components/__tests__/documentationGeneration.test.tsx`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/admin-ui/app/components/ClaudeCodeSessionForm.tsx services/admin-ui/app/components/DocumentationWorkflowView.tsx \
  services/admin-ui/app/components/__tests__/claudeCodeSessionForm.test.tsx
git commit -m "feat(admin-ui): New documentation with an agent starts an interactive Claude Code session

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: admin-ui — the session page

**Files:**
- Create: `services/admin-ui/app/components/ClaudeCodeSessionView.tsx`
- Create: `services/admin-ui/app/routes/documentationSession.tsx`
- Modify: `services/admin-ui/app/routes.ts`
- Create: `services/admin-ui/app/components/__tests__/claudeCodeSession.test.tsx`

**Interfaces:**
- Consumes: Task 9's session API and types; `Markdown`; `SAME_DOCUMENT_NAME`.
- Produces: `ClaudeCodeSessionView({ sessionId })`; route `documentation/sessions/:sessionId`.

- [ ] **Step 1: Write the failing test**

Create `services/admin-ui/app/components/__tests__/claudeCodeSession.test.tsx`:

```tsx
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
vi.mock('../Markdown', () => ({ default: ({ content }: { content: string }) => <div data-testid="markdown">{content}</div> }));
import ClaudeCodeSessionView from '../ClaudeCodeSessionView';
import { SAME_DOCUMENT_NAME } from '~/lib/documents';

class Stream extends EventTarget {
  static instances: Stream[] = [];
  close = vi.fn();
  constructor(public url: string) { super(); Stream.instances.push(this); }
  emit(type: string, value: unknown) { this.dispatchEvent(new MessageEvent(type, { data: JSON.stringify(value) })); }
}
beforeEach(() => { Stream.instances = []; vi.stubGlobal('EventSource', Stream); });
afterEach(() => vi.unstubAllGlobals());
const state = { status: 'starting', status_message: '', turns: 0, input_tokens: 0, output_tokens: 0, cached_tokens: 0, cost_usd: 0,
  draft: null, draft_turn: null, document_id: null, worker_active: false, queued: 1 };
const detail = { ...state, id: 's1', title: 'Payments – API reference', document_name: 'Payments – API reference', folder: 'payments-api-docs',
  agent_id: 'writer', agent_name: 'Technical writer', model_id: 'claude', model_name: 'Claude on Bedrock', document_title: null,
  prompt_snapshot: { id: 'p', name: 'Code analysis', version: 3, comments: '', system_prompt: 'You are an expert technical writer.' },
  context_snapshot: { folders: [{ path: '/work/data/code/app', kind: 'code_project', name: 'Payments API' }], code_project_ids: ['repo'], servers: [], notes: '' },
  inputs: [], created_at: '2026-10-07T10:00:00Z', last_activity_at: '2026-10-07T10:00:00Z', closed_at: null };

test('the session page streams activity, sends messages, stops, saves the draft and closes', async () => {
  const posts: { url: string; body?: unknown }[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method !== 'POST') return Response.json(detail);
    const body = init.body ? JSON.parse(init.body as string) : undefined;
    posts.push({ url, body });
    if (url.endsWith('/messages')) return Response.json({ seq: 2, status: 'queued', working: true }, { status: 202 });
    if (url.endsWith('/stop')) return Response.json({ id: 's1', discarded: 0 }, { status: 202 });
    if (url.endsWith('/document')) return body.update_existing
      ? Response.json({ id: 'doc', revision: 2, version_id: 'v2', folder: 'payments-api-docs', output_folder: '/work/data/generated-documents/payments-api-docs/payments-api-reference', export_error: null }, { status: 201 })
      : Response.json({ error: { message: SAME_DOCUMENT_NAME } }, { status: 409 });
    return Response.json({ id: 's1' });
  }));
  render(<MemoryRouter><ClaudeCodeSessionView sessionId="s1" /></MemoryRouter>);
  expect(await screen.findByText('Starting')).toBeVisible();
  expect(screen.getByText(/Waiting for the Claude Code worker/)).toBeVisible();
  const [stream] = Stream.instances;
  expect(stream.url).toBe('/api/claude-code/sessions/s1/events');
  act(() => {
    stream.emit('session', { ...state, status: 'working', turns: 1, worker_active: true, queued: 0 });
    stream.emit('run_event', { id: 1, turn: 1, seq: 3, kind: 'input', payload: { task: 'Document the payments API.' } });
    stream.emit('run_event', { id: 2, turn: 1, seq: 4, kind: 'reasoning', payload: { text: 'Start with the routes.' } });
    stream.emit('run_event', { id: 3, turn: 1, seq: 5, kind: 'tool_call', payload: { name: 'Read', summary: '/work/data/code/app/main.py' } });
    stream.emit('run_event', { id: 4, turn: 1, seq: 6, kind: 'tool_result', payload: { name: 'Bash', preview: 'total 8\ndrwxr-xr-x app', is_error: false } });
    stream.emit('run_event', { id: 4, turn: 1, seq: 6, kind: 'tool_result', payload: { name: 'Bash', preview: 'total 8\ndrwxr-xr-x app', is_error: false } });
  });
  expect(screen.getByText('Working')).toBeVisible();
  expect(screen.queryByText(/Waiting for the Claude Code worker/)).toBeNull();
  const activity = within(screen.getByRole('region', { name: 'Agent activity' }));
  expect(activity.getByText('Document the payments API.')).toBeVisible();
  expect(activity.getByText('Start with the routes.')).toBeVisible();
  expect(activity.getByText('/work/data/code/app/main.py')).toBeVisible();
  expect(activity.getAllByText(/drwxr-xr-x app/)).toHaveLength(1);  // a replayed event is shown once
  expect(screen.getByRole('button', { name: 'Save document' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'Also cover retries.' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
  expect(await screen.findByText(/^Queued\./)).toBeVisible();
  expect(screen.getByLabelText('Message')).toHaveValue('');
  fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
  await waitFor(() => expect(posts.map(p => p.url)).toContain('/api/claude-code/sessions/s1/stop'));
  act(() => {
    stream.emit('run_event', { id: 5, turn: 1, seq: 7, kind: 'message', payload: { text: '# Payments API' } });
    stream.emit('session', { ...state, status: 'waiting', turns: 1, worker_active: true, queued: 0, draft: '# Payments API\n\nRetries back off.',
      draft_turn: 1, input_tokens: 1050, output_tokens: 40, cached_tokens: 900 });
  });
  expect(screen.getByText('Waiting for you')).toBeVisible();
  expect(within(screen.getByRole('region', { name: 'Draft' })).getByTestId('markdown')).toHaveTextContent('# Payments API Retries back off.');
  expect(screen.getByText('In 1,050 · Out 40 · Cached 900')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Stop' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Save document' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Save new version' }));
  expect(await screen.findByText(/Saved as version 2 in payments-api-docs/)).toBeVisible();
  expect(screen.getByRole('link', { name: 'Open document' })).toHaveAttribute('href', '/documents/doc');
  expect(posts.filter(p => p.url.endsWith('/document')).map(p => p.body)).toEqual([{ update_existing: false }, { update_existing: true }]);
  fireEvent.click(screen.getByRole('button', { name: 'Close session' }));
  await waitFor(() => expect(posts.map(p => p.url)).toContain('/api/claude-code/sessions/s1/close'));
  act(() => { stream.emit('session', { ...state, status: 'closed', turns: 1, draft: '# Payments API\n\nRetries back off.' }); stream.emit('closed', {}); });
  expect(screen.getByText('Finished')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Send message' })).toBeDisabled();
  expect(stream.close).toHaveBeenCalled();
});
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `cd services/admin-ui && pnpm exec vitest run app/components/__tests__/claudeCodeSession.test.tsx`
Expected: FAIL: `Failed to resolve import "../ClaudeCodeSessionView"`.

- [ ] **Step 3: Create `services/admin-ui/app/components/ClaudeCodeSessionView.tsx`**

```tsx
import { useEffect, useState } from 'react';
import { Link } from 'react-router';
import { ApiError } from '~/lib/api';
import { closeSession, formatTokens, getSession, saveSessionDocument, sendMessage, sessionEventsUrl, statusLabels, stopSession,
  type SavedDocument, type SessionDetail, type SessionEvent, type SessionState } from '~/lib/claudeCode';
import { SAME_DOCUMENT_NAME } from '~/lib/documents';
import Markdown from './Markdown';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle } from '~/components/ui/alert-dialog';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { Field, FieldLabel } from '~/components/ui/field';
import { Skeleton } from '~/components/ui/skeleton';
import { Textarea } from '~/components/ui/textarea';

function EventLine({ event }: { event: SessionEvent }) {
  const payload = event.payload as Record<string, unknown>;
  const text = (key: string) => String(payload[key] ?? '');
  switch (event.kind) {
    case 'input': return <div className="rounded-md bg-muted p-3"><p className="text-xs font-medium text-muted-foreground">You · turn {event.turn}</p>
      <p className="line-clamp-6 whitespace-pre-wrap text-sm">{text('task')}</p></div>;
    case 'message': return <p className="whitespace-pre-wrap text-sm">{text('text')}</p>;
    case 'reasoning': return <p className="whitespace-pre-wrap text-sm italic text-muted-foreground">{text('text')}</p>;
    case 'tool_call': return <p className="text-sm"><Badge variant="outline" className="mr-2">{text('name')}</Badge><code className="break-all">{text('summary')}</code></p>;
    case 'tool_result': return payload.preview ? <pre className={`max-h-48 overflow-auto rounded-md border p-2 text-xs${payload.is_error ? ' text-destructive' : ''}`}>{text('preview')}</pre> : null;
    case 'error': return <p className="text-sm text-destructive">{text('message')}</p>;
    case 'end': return <p className="text-xs text-muted-foreground">Turn {event.turn} {text('status')}</p>;
    default: return null;
  }
}

/** One interactive Claude Code session: the live stream, messages, Stop, the draft, Save document and Close session. */
export default function ClaudeCodeSessionView({ sessionId }: { sessionId: string }) {
  const [session, setSession] = useState<SessionDetail | null>(null);
  const [events, setEvents] = useState<SessionEvent[]>([]);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [saved, setSaved] = useState<SavedDocument | null>(null);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    setSession(null); setEvents([]); setError('');
    getSession(sessionId, controller.signal).then(setSession)
      .catch(e => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Could not open the session'); });
    // Leaving the page closes only this stream; the session keeps running.
    const stream = new EventSource(sessionEventsUrl(sessionId));
    stream.addEventListener('run_event', e => {
      const event = JSON.parse((e as MessageEvent).data) as SessionEvent;
      setEvents(current => current.some(item => item.id === event.id) ? current : [...current, event]);
    });
    stream.addEventListener('session', e => {
      const next = JSON.parse((e as MessageEvent).data) as SessionState;
      setSession(current => current && { ...current, ...next });
    });
    stream.addEventListener('closed', () => stream.close());
    stream.addEventListener('unavailable', () => setError('The session stream is unavailable. Reload the page.'));
    return () => { controller.abort(); stream.close(); };
  }, [sessionId]);

  async function run(action: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('');
    try { await action(); } catch (e) { setError(e instanceof Error ? e.message : 'The request failed'); } finally { setBusy(false); }
  }
  const send = () => run(async () => {
    const result = await sendMessage(sessionId, message.trim());
    setMessage('');
    setNotice(result.working ? 'Queued. The agent gets your message when it finishes its current response.' : 'Sent. The agent starts a new turn with your message.');
  });
  const stop = () => run(async () => { await stopSession(sessionId); setNotice('Stopping the current turn…'); });
  const close = () => run(async () => { await closeSession(sessionId); setSession(current => current && { ...current, status: 'closed' }); });
  async function save(updateExisting: boolean) {
    setBusy(true); setError(''); setNotice('');
    try {
      const result = await saveSessionDocument(sessionId, updateExisting);
      setConfirming(false); setSaved(result);
      setSession(current => current && { ...current, document_id: result.id });
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && e.message === SAME_DOCUMENT_NAME && !updateExisting) setConfirming(true);
      else { setConfirming(false); setError(e instanceof Error ? e.message : 'Could not save the document'); }
    } finally { setBusy(false); }
  }

  if (!session) return error ? <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert> : <Skeleton className="h-60" />;
  const closed = session.status === 'closed';
  return <div className="flex flex-col gap-5">
    <Link className="underline" to="/documentation/sessions">Sessions</Link>
    <div className="flex flex-wrap items-center gap-3"><h1 className="text-2xl font-semibold">{session.title}</h1>
      <Badge variant={session.status === 'failed' ? 'destructive' : 'secondary'}>{statusLabels[session.status]}</Badge></div>
    <p className="text-sm text-muted-foreground">{session.agent_name} · {session.model_name} · {session.folder}/</p>
    <p className="text-sm">{`In ${formatTokens(session.input_tokens)} · Out ${formatTokens(session.output_tokens)} · Cached ${formatTokens(session.cached_tokens)}`}</p>
    {session.status === 'starting' && !session.worker_active && <Alert><AlertDescription>Waiting for the Claude Code worker. The session starts when the worker is running.</AlertDescription></Alert>}
    {session.status_message && ['failed', 'stopped'].includes(session.status) && <Alert variant={session.status === 'failed' ? 'destructive' : 'default'}><AlertDescription>{session.status_message}</AlertDescription></Alert>}
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    {notice && <Alert><AlertDescription role="status">{notice}</AlertDescription></Alert>}
    {saved && <Alert><AlertDescription>Saved as version {saved.revision} in {saved.folder}. <Link className="underline" to={`/documents/${saved.id}`}>Open document</Link>{saved.export_error ? ` ${saved.export_error}` : ''}</AlertDescription></Alert>}
    <div className="grid gap-5 lg:grid-cols-2">
      <section aria-label="Agent activity" className="flex min-w-0 flex-col gap-3 rounded-lg border p-4"><h2 className="font-semibold">Activity</h2>
        {events.length === 0 && <p className="text-sm text-muted-foreground">The agent's messages, thinking and tool calls appear here.</p>}
        {events.map(event => <EventLine key={event.id} event={event} />)}
        {session.queued > 0 && <p className="text-sm text-muted-foreground">{session.queued} message{session.queued === 1 ? '' : 's'} queued</p>}</section>
      <section aria-label="Draft" className="flex min-w-0 flex-col gap-3 rounded-lg border p-4"><h2 className="font-semibold">Draft{session.draft_turn ? ` · turn ${session.draft_turn}` : ''}</h2>
        {session.draft ? <Markdown content={session.draft} /> : <p className="text-sm text-muted-foreground">The draft appears when the agent finishes a turn.</p>}</section>
    </div>
    <form className="flex flex-col gap-3" onSubmit={e => { e.preventDefault(); void send(); }}>
      <Field><FieldLabel htmlFor="session-message">Message</FieldLabel><Textarea id="session-message" rows={3} maxLength={20000} disabled={closed} value={message} onChange={e => setMessage(e.target.value)} placeholder="For example: shorten the API section" /></Field>
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={busy || closed || !message.trim()}>Send message</Button>
        <Button type="button" variant="outline" disabled={busy || session.status !== 'working'} onClick={() => void stop()}>Stop</Button>
        <Button type="button" variant="outline" disabled={busy || !session.draft} onClick={() => void save(false)}>Save document</Button>
        <Button type="button" variant="outline" disabled={busy || closed} onClick={() => void close()}>Close session</Button>
      </div>
    </form>
    <AlertDialog open={confirming} onOpenChange={setConfirming}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>Save a new version?</AlertDialogTitle>
      <AlertDialogDescription>{`${session.folder} already has a document named “${session.document_name}”. Saving adds a new version of it; earlier versions stay available.`}</AlertDialogDescription></AlertDialogHeader>
      <AlertDialogFooter><AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel><AlertDialogAction disabled={busy} onClick={e => { e.preventDefault(); void save(true); }}>Save new version</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
  </div>;
}
```

Create `services/admin-ui/app/routes/documentationSession.tsx`:

```tsx
import { useParams } from 'react-router';
import AdminLayout from '../components/AdminLayout';
import ClaudeCodeSessionView from '../components/ClaudeCodeSessionView';

export default function Page() {
  const { sessionId } = useParams();
  return <AdminLayout><ClaudeCodeSessionView key={sessionId} sessionId={sessionId!} /></AdminLayout>;
}
```

In `services/admin-ui/app/routes.ts`, after the `documentation/generations/:workflowId` route, add:

```ts
  route("documentation/sessions/:sessionId", "./routes/documentationSession.tsx"),
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd services/admin-ui && pnpm exec tsc --noEmit && pnpm exec vitest run app/components/__tests__/claudeCodeSession.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-ui/app/components/ClaudeCodeSessionView.tsx services/admin-ui/app/routes/documentationSession.tsx \
  services/admin-ui/app/routes.ts services/admin-ui/app/components/__tests__/claudeCodeSession.test.tsx
git commit -m "feat(admin-ui): Claude Code session page with live stream, messages, stop, draft and save

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: admin-ui — Docs → Sessions

**Files:**
- Create: `services/admin-ui/app/components/ClaudeCodeSessionsView.tsx`
- Create: `services/admin-ui/app/routes/documentationSessions.tsx`
- Modify: `services/admin-ui/app/routes.ts`
- Modify: `services/admin-ui/app/components/AdminLayout.tsx`
- Modify: `services/admin-ui/app/components/__tests__/adminLayout.test.tsx`
- Create: `services/admin-ui/app/components/__tests__/claudeCodeSessions.test.tsx`

**Interfaces:**
- Consumes: Task 9's `getSessions`, `statusLabels`, `formatTokens`.
- Produces: `ClaudeCodeSessionsView()`; route `documentation/sessions`; sidebar link **Sessions**.

- [ ] **Step 1: Write the failing tests**

Create `services/admin-ui/app/components/__tests__/claudeCodeSessions.test.tsx`:

```tsx
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, expect, test, vi } from 'vitest';
import AdminLayout from '../AdminLayout';
import ClaudeCodeSessionsView from '../ClaudeCodeSessionsView';

afterEach(() => vi.unstubAllGlobals());
const row = { id: 's1', title: 'Payments – API reference', agent_id: 'writer', agent_name: 'Technical writer', model_name: 'Claude on Bedrock',
  status: 'waiting', status_message: '', turns: 2, input_tokens: 12345, output_tokens: 678, cached_tokens: 9000, cost_usd: 0.4,
  document_id: 'doc', document_title: 'Payments – API reference', folder: 'payments-api-docs', worker_active: true, queued: 0,
  created_at: '2026-10-07T10:00:00Z', last_activity_at: '2026-10-07T11:00:00Z', closed_at: null };

test('Docs → Sessions lists sessions with status, agent, tokens and document', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.startsWith('/api/claude-code/sessions')
    ? Response.json({ sessions: [row, { ...row, id: 's2', title: 'Jira overview', status: 'closed', document_id: null, document_title: null }] })
    : Response.json({ is_admin: true })));
  render(<MemoryRouter initialEntries={['/documentation/sessions']}><AdminLayout><ClaudeCodeSessionsView /></AdminLayout></MemoryRouter>);
  const first = (await screen.findByRole('link', { name: 'Payments – API reference' })).closest('tr')!;
  expect(within(first).getByRole('link', { name: 'Payments – API reference' })).toHaveAttribute('href', '/documentation/sessions/s1');
  expect(within(first).getByText('Waiting for you')).toBeVisible();
  expect(within(first).getByText('Technical writer')).toBeVisible();
  expect(within(first).getByText('12,345 in · 678 out')).toBeVisible();
  expect(within(first).getAllByRole('link').map(link => link.getAttribute('href'))).toContain('/documents/doc');
  const second = screen.getByRole('link', { name: 'Jira overview' }).closest('tr')!;
  expect(within(second).getByText('Finished')).toBeVisible();
  expect(within(second).getByText('Not saved')).toBeVisible();
  expect(screen.getByRole('link', { name: 'New session' })).toHaveAttribute('href', '/documentation/generations/new?scope=agent');
  const docs = within(screen.getByRole('navigation', { name: 'Docs' }));
  expect(docs.getAllByRole('link').map(link => link.textContent)).toEqual(['Documents', 'Prompts', 'Generate documents', 'Sessions', 'Folders']);
  expect(docs.getByRole('link', { name: 'Sessions' })).toHaveAttribute('href', '/documentation/sessions');
  expect(screen.getByRole('heading', { name: 'Documentation sessions' })).toBeVisible();
});
```

In `services/admin-ui/app/components/__tests__/adminLayout.test.tsx`, after the **Generate documents** assertion add:

```tsx
  expect(docs.getByRole("link", { name: "Sessions" })).toHaveAttribute("href", "/documentation/sessions");
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-ui && pnpm exec vitest run app/components/__tests__/claudeCodeSessions.test.tsx app/components/__tests__/adminLayout.test.tsx`
Expected: FAIL: `Failed to resolve import "../ClaudeCodeSessionsView"` and no **Sessions** link.

- [ ] **Step 3: Implement**

Create `services/admin-ui/app/components/ClaudeCodeSessionsView.tsx`:

```tsx
import { useEffect, useState } from 'react';
import { Link } from 'react-router';
import { formatTokens, getSessions, statusLabels, type SessionSummary } from '~/lib/claudeCode';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { Skeleton } from '~/components/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '~/components/ui/table';

/** Interactive Claude Code sessions, newest first; leaving a session page does not stop it. */
export default function ClaudeCodeSessionsView() {
  const [rows, setRows] = useState<SessionSummary[] | null>(null);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    getSessions(offset, controller.signal).then(result => { if (!controller.signal.aborted) setRows(result.sessions); })
      .catch(e => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Could not load sessions'); });
    return () => controller.abort();
  }, [offset]);
  return <div className="flex flex-col gap-5">
    <div className="flex flex-wrap items-center justify-between gap-3"><h1 className="text-2xl font-semibold">Sessions</h1>
      <Button asChild><Link to="/documentation/generations/new?scope=agent">New session</Link></Button></div>
    <p className="text-muted-foreground">Interactive documentation sessions with Claude Code agents. Leaving a session page does not stop it. Sessions idle for 24 hours are closed; their history is kept.</p>
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    {rows === null ? <Skeleton className="h-40" /> : <Table><TableHeader><TableRow><TableHead>Session</TableHead><TableHead>Status</TableHead><TableHead>Agent</TableHead>
      <TableHead>Tokens</TableHead><TableHead>Document</TableHead><TableHead>Last activity</TableHead></TableRow></TableHeader><TableBody>
      {rows.map(row => <TableRow key={row.id}>
        <TableCell><Link className="underline" to={`/documentation/sessions/${row.id}`}>{row.title}</Link><p className="text-xs text-muted-foreground">{row.folder}/</p></TableCell>
        <TableCell><Badge variant={row.status === 'failed' ? 'destructive' : 'secondary'}>{statusLabels[row.status]}</Badge></TableCell>
        <TableCell><span>{row.agent_name}</span><p className="text-xs text-muted-foreground">{row.model_name}</p></TableCell>
        <TableCell>{`${formatTokens(row.input_tokens)} in · ${formatTokens(row.output_tokens)} out`}</TableCell>
        <TableCell>{row.document_id ? <Link className="underline" to={`/documents/${row.document_id}`}>{row.document_title}</Link> : 'Not saved'}</TableCell>
        <TableCell>{new Date(row.last_activity_at).toLocaleString()}</TableCell>
      </TableRow>)}
    </TableBody></Table>}
    {rows?.length === 0 && <p>No sessions yet. Start one with New session.</p>}
    {(offset > 0 || rows?.length === 50) && <div className="flex gap-2"><Button variant="outline" disabled={!offset} onClick={() => setOffset(n => n - 50)}>Previous</Button>
      <Button variant="outline" disabled={(rows?.length ?? 0) < 50} onClick={() => setOffset(n => n + 50)}>Next</Button></div>}
  </div>;
}
```

Create `services/admin-ui/app/routes/documentationSessions.tsx`:

```tsx
import AdminLayout from '../components/AdminLayout';
import ClaudeCodeSessionsView from '../components/ClaudeCodeSessionsView';

export default function Page() {
  return <AdminLayout><ClaudeCodeSessionsView /></AdminLayout>;
}
```

In `services/admin-ui/app/routes.ts`, before the `documentation/sessions/:sessionId` route, add
`route("documentation/sessions", "./routes/documentationSessions.tsx"),`.

In `services/admin-ui/app/components/AdminLayout.tsx`:
- import `MessagesSquare` from `lucide-react`;
- add `const docsSessions = pathname.startsWith('/documentation/sessions');`;
- in the title expression use `docsHistory ? "Generate documentation" : docsSessions ? "Documentation sessions" : docsFolders ? …`;
- in the Docs sidebar group, between **Generate documents** and **Folders**, add:

```tsx
          <SidebarMenuItem><SidebarMenuButton asChild isActive={docsSessions}><Link to="/documentation/sessions"><MessagesSquare /><span>Sessions</span></Link></SidebarMenuButton></SidebarMenuItem>
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd services/admin-ui && pnpm exec tsc --noEmit && pnpm exec vitest run app/components/__tests__/claudeCodeSessions.test.tsx app/components/__tests__/adminLayout.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-ui/app/components/ClaudeCodeSessionsView.tsx services/admin-ui/app/routes/documentationSessions.tsx \
  services/admin-ui/app/routes.ts services/admin-ui/app/components/AdminLayout.tsx \
  services/admin-ui/app/components/__tests__/adminLayout.test.tsx services/admin-ui/app/components/__tests__/claudeCodeSessions.test.tsx
git commit -m "feat(admin-ui): Docs → Sessions lists interactive Claude Code sessions

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 13: User guide and full verification

**Files:**
- Modify: `docs/user-guide.md`

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write the docs**

`docs/user-guide.md`:
- **§1 Architecture at a glance**: add `claude-code-worker` (Claude Agent SDK with its bundled Claude Code CLI; reads
  `DATA_ROOT` read-only; network access to Bedrock or Anthropic; its home volume `claude-code-home` is its only
  writable mount).
- **§5.1 Navigation**: the Docs row becomes `Documents · Prompts · Generate documents · Sessions · Folders`.
- **§5.11 Agents**: add **Claude Code agents**: **All agents** shows them with a **Claude Code SDK** badge; **New
  Claude Code agent** asks only for a name and a description; the agent page edits only those. The model, folders,
  prompt and context are chosen per session. Tools are fixed: Read, Grep, Glob and Bash in their own container with
  `DATA_ROOT` read-only; Bash can reach the network; no Edit/Write, MCP or sub-agents; no turn, time or cost limits.
  They never appear as the Reader assistant, on the multi-agent board or in test chats.
- **§5.13 Documentation generation**: add **With an agent** (interactive) with:
  - the form: **Agent**, **Model** (required, Claude connections only: Bedrock inference profiles such as
    `eu.anthropic.claude-…` or the Anthropic API), **Prompt** (any active documentation prompt), **Context** (code
    projects, folders under `DATA_ROOT` with **Choose a folder**, servers as name/address/description text, notes),
    **Comments**, **Document name** and **Documentation folder** (default `<first-project>-docs` or `docs`) →
    **Generate** opens the session page;
  - what the agent gets: the built-in system prompt (quote it), one message with the prompt, comments and context (the
    folders as absolute container paths; the first folder is its working directory); no SSH or server credentials;
  - the session page: live activity (messages, thinking, tool calls with path, pattern or command and the first lines
    of output), tokens in/out/cached, status Starting · Working · Waiting for you · Stopped · Failed · Finished;
    **Send message** (while working, the message is queued and delivered when the current response finishes; after a
    turn it starts a new turn in the same conversation); **Stop** (interrupts the turn and drops queued messages);
    **Draft** (each finished turn's final answer, as Markdown); **Save document** (a versioned document in the folder,
    linked to the code projects, exported to `GENERATED_DOCS_FOLDER/<folder>/<slug>/README.md`; same name asks **Save
    a new version?**; nothing is saved automatically); **Close session**;
  - leaving the page does not stop a session; sessions idle for 24 hours are closed and keep their history; every turn
    is an agent run in **Activity → Agent runs** (`cc:<session>:<turn>`);
  - errors: "Select an enabled Claude model (Bedrock or Anthropic) from LLM Settings."; a provider error such as a
    missing Bedrock inference profile shows the provider's reason without credentials plus a hint; "Waiting for the
    Claude Code worker…" when the worker is not running; a folder that no longer exists is refused when starting.
- **§5.14 Documents**: add **Sessions** (`/documentation/sessions`): status, agent, model, tokens, document and last
  activity; **New session**.
- **§6**: add **H. Write documentation interactively with Claude Code**: Agents → New Claude Code agent → Docs →
  Generate documents → New documentation → With an agent → Generate → follow the stream, send "shorten the API section"
  → Save document → Docs → Folders → Index in RAG → Collections.
- **§7 Configuration**: `CLAUDE_CODE_MAX_SESSIONS` (default 4, running Claude Code processes per worker).
- **Appendix A**: Claude Code agents: Read, Grep, Glob and Bash as user `claude` (uid 1001) in `claude-code-worker`,
  `DATA_ROOT` read-only, `setting_sources=[]` (no `.claude/` settings, hooks or CLAUDE.md from the folders), the model
  key only in the CLI environment.

- [ ] **Step 2: Run every suite**

Run:
```bash
cd services/admin-api && uv run --frozen pytest -q
cd ../rag && uv run --frozen pytest -q
cd ../admin-ui && pnpm exec tsc --noEmit && pnpm test
cd ../.. && python3 -m unittest discover -s tests && bash db/tests/run.sh
```
Expected: everything passes. For any failure, check out `main` in a separate worktree and run the same test. Accept it
only if it also fails there, and record it in the PR description. Never run `make setup`, `make compose-validate`,
`make up` or `make smoke`.

- [ ] **Step 3: Check for leftovers**

Run: `git grep -n "claude_agent_sdk" -- services/admin-api/src`
Expected: only `claude_code_worker.py` (inside `sdk_client`) and `claude_code_cli.py` (the `find_spec` string).

Run: `git grep -n "AWS_BEARER_TOKEN_BEDROCK\|ANTHROPIC_API_KEY" -- services deploy`
Expected: only `services/admin-api/src/knowledge_admin/claude_code_worker.py` and its tests.

Run: `git grep -n "FROM ka.agents" -- services/admin-api/src/knowledge_admin/reader_settings.py services/admin-api/src/knowledge_admin/board.py`
Expected: the Reader candidate query filters `runtime='openai'`; the board submit selects `a.runtime`.

Run: `git diff main --stat -- services/rag`
Expected: no changes.

- [ ] **Step 4: Commit**

```bash
git add docs/user-guide.md
git commit -m "docs: Claude Code agents and interactive documentation sessions

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```
