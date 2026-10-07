# Knowledge Agents Step 3 — Native Agent Sessions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Native agents can write documentation again, through the same session machinery as Claude Code agents, so
the two runtimes can be compared on the same request. **Docs → Generate documents → New** lists every enabled agent
with a model, of both types, with its type. Submitting creates the same session as today: same table, same
`/api/claude-code/sessions*` routes (create, list, detail, events SSE, `events/history`, send, stop, close, Save
document), same first message (`generate_documents.first_message`), same session page. A native session's turns run in
a new `agent-session-worker` with the agent's saved config (pinned when the session starts) through the existing native
runtime (`knowledge_admin.agents.agent`). The session's working folders (chosen code projects, the code projects linked
to chosen environments and services, and the agent's own code projects) become the native agent's code roots, so
`list_files`, `read_file`, `search_files`, `shell_run`, `code_search` and `ast_grep` can read them. Credentials are
decrypted only in the worker, per turn, and redacted from events, the draft, errors and history. The conversation is
kept through an Agents SDK `Session` implementation backed by the session row: `get_items()` reads the saved history,
`add_items()` only buffers, and the buffer is committed, redacted, in the transaction that records a successful turn,
and dropped when the turn fails, is stopped or times out. The draft is the final answer of each turn. Stop cancels the
running turn. Native turns write the same run events (`input`, `message`, `reasoning`, `tool_call` with a summary,
`tool_result` with a preview, `usage`, `error`, `end`) under the same run keys, so the session page and **Activity →
Agent runs** work unchanged.

**Architecture:**
- **Schema (migration 0074 `native_agent_sessions`):** `ka.agent_sessions_cc` gets `runtime` (`openai` | `claude_code`,
  default `claude_code`), `agent_config jsonb` (native only: the pinned agent config with the session's code roots,
  credential references only) and `history jsonb` (native only: the Agents SDK items of successful turns, redacted).
  A check ties `agent_config` to native sessions. The down migration refuses while native sessions exist.
- **Shared session machinery:** a new module `knowledge_agents/session_worker.py` holds what both workers share and
  what `claude_code_worker.py` has today: `claim(…, runtime)` (lease, renewal, interrupted-turn recovery, now per agent
  type), the `SessionRunner` base (idle release, `next_turn` delivering queued messages once, `stop_requested`,
  `finish_turn` with an optional in-transaction `commit`, `release`), `serve(…)` (slots, reclaim, claim loop), the text
  helpers (`clean_text`, `capped`, `scrub`) and `run_key(session_id, turn)` (`cc:<session>:<turn>`, unchanged).
  `claude_code_worker` keeps its Claude Code parts (options, slots, Turn, client) and its public names (`claim`,
  `serve`, `SessionRunner`, constants) as thin wrappers, so its tests pin the refactor.
- **Session API (`claude_code_api.py`):** `POST /api/claude-code/sessions` accepts any enabled, non-coordinator agent
  whose model fits its type (`knowledge_agents.config.model_fits`). Claude Code sessions are created exactly as today.
  Native sessions store `runtime='openai'` and `agent_config = native_sessions.code_roots(config, folders)`; they need no
  working folder. Listing and detail return `runtime`; they never return `agent_config` or `history`.
- **Native history (`knowledge_agents/history.py`):** `HistorySession`, an Agents SDK `Session` loaded per turn from
  `agent_sessions_cc.history`; `commit(conn, secrets)` appends the scrubbed buffer inside the caller's transaction;
  `drop()` discards it. `agents.agent()` gains `session=` and `hooks=` (a session turn passes no `history` and runs with
  `stream=False`, so the SDK has added every item when `Runner.run` returns).
- **Native turn events (`knowledge_agents/native_sessions.py`):** `SessionEvents(RunHooks)` writes a native turn's SDK
  lifecycle as the events a Claude Code turn writes (tool call summaries, capped tool output with a 20-line preview,
  one usage event per turn) and keeps the turn's message texts for the draft fallback. `agent_activity.tool_failed`
  is extracted from `SdkActivity` and shared.
- **agent-session-worker (`knowledge_agents/native_sessions.py`, script `knowledge-agent-session-worker`):**
  `NativeRunner(shared.SessionRunner)` runs a turn: load the pinned config, decrypt its credentials
  (`agents.load_credentials`), load `HistorySession`, run `agents.agent(config, text, credentials=…, session=…,
  hooks=…, timeout_seconds=300, max_turns=20, stream=False)` as a task, cancel it on Stop/close/lost lease, then
  `finish_turn` with the draft, the tokens and the history commit. A compose service `agent-session-worker` on
  `default` and `agent-shell` (like `admin-api`: RAG, PostgreSQL, MCP servers, model providers, `shell-worker`), with
  `DATA_ROOT` read-only and the `agent-workspaces` volume, Phoenix tracing as in `admin-api`.
- **admin-ui:** the Generate documents form lists enabled non-coordinator agents with a model, both types, labelled
  `<name> (Native)` / `<name> (Claude Code)`; the list shows each session's type; the session page names the right
  worker while waiting; **Agent runs** labels the `native_session` run type.

**Tech Stack:**
- PostgreSQL 17 with pgvector, golang-migrate SQL migrations, `db/tests/run.sh`;
- FastAPI, pydantic v2, psycopg3, OpenAI Agents SDK 0.19.2 (`agents.memory.Session`, `RunHooks`, `Runner.run`)
  (`services/admin-api`), pytest against a disposable PostgreSQL container (`knowledge-agent-v3-postgres:17.10-vector0.8.6`);
- Docker Compose (`deploy/compose.yaml`, checked by `tests/test_setup.py` with `docker compose config` and a temp `.env`);
- React Router 7 with shadcn/ui, Vitest with Testing Library (`services/admin-ui`).

**Spec:** `docs/superpowers/specs/2026-10-07-knowledge-agents-design.md`, sections **Session API for both types**
(including "Native history uses the SDK's `Session` interface") and **Delivery steps → 3. Native agents through the
session API** (binding). This plan delivers the native session worker, the SDK Session history, both agent types in
Generate documents and the shared event format. Where it differs from the spec's letter, see **Decisions**.

## Amendment (user decision, 2026-10-08): no turn limits, Stop always works

This overrides every mention of 300 s / 20 model calls below (Global Constraints, Tasks 6 and 9, tests, docs).
- Native session turns have **no total time limit and no model-call limit**, like Claude Code sessions. They end when
  the agent finishes or the user presses **Stop** (or closes the session / the lease is lost).
- `agents.agent()` gets `turn_timeout_seconds: float | None = None`, separate from `timeout_seconds`, which keeps its
  current meaning for each model request and MCP connection (stays 300 s). The whole-turn `asyncio.timeout` uses
  `turn_timeout_seconds` (None = no limit). Existing callers keep their behaviour by passing
  `turn_timeout_seconds=timeout_seconds` where they relied on it (test chats, board, wizard) — verify each call site.
- Native session turns call `agent(..., max_turns=None, turn_timeout_seconds=None)`; the SDK treats `max_turns=None` as
  unlimited (`run_internal/run_loop.py`). Remove `TURN_TIMEOUT_SECONDS`, `MAX_TURNS`, `TOO_MANY_STEPS` and the timeout
  error text from the native runner; tests assert the runner passes `max_turns=None, turn_timeout_seconds=None`.
- **Stop must always work**, at any point of a turn: before the first model call, during a model call, during a tool
  call (including `shell_run` and `ssh_run`), and while waiting for MCP. Stop cancels the running task; the turn ends as
  `stopped`, its history is dropped, the session stays usable for the next message. Add tests for Stop during a model
  call and during a slow tool call (fake runtime), and that a stopped turn leaves no running work behind.
- Test chats and the board keep their current limits (out of scope).
- **Comments while the agent works (native):** a message sent while a native turn is running is delivered **inside
  that turn**, before the agent's next model call, instead of waiting for the turn to end. Use the SDK's
  `RunConfig(call_model_input_filter=…)`: before each model call, claim the session's queued `agent_session_inputs`
  (same `delivered` marking as `next_turn`, guarded by the worker's lease), append each as a user message
  ("Comment from the user while you work: …") to the model input, record an `input` event for it, and include it in
  the turn's buffered history. Messages that arrive after the last model call are delivered as the next turn, as today.
  Tests: a message queued during a slow tool call reaches the following model call of the same turn, exactly once,
  with an `input` event; a message after the final answer starts the next turn.
- The session page keeps the message box enabled while the agent works and says "Your comment reaches the agent at its
  next step" for native sessions (Claude Code keeps "after its current response").

## Global Constraints

- Implementers and reviewers never run make targets and never touch .env or .local; compose checks use a temp .env.
  Never touch the running Docker stack's database: tests use the disposable PostgreSQL fixture and `db/tests/run.sh`.
- Credentials never logged, stored in events or history unredacted, or passed to Bash. The API never decrypts a
  credential for a session: `agent_config` holds credential references only (`api_key_ref`, `private_key_ref`,
  `bearer_token_ref`); the worker decrypts per turn with `agents.load_credentials`, clears them after the turn, and
  redacts every decrypted value from events (`RunRecorder` secrets), tool output (`clean_text` before capping), the
  draft, the status message and history (`scrub` in `HistorySession.commit`). Logs carry exception type names only.
- Commit trailer: `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Python stays compatible with 3.12.
- Migration `0074_native_agent_sessions` (`.up.sql` and `.down.sql`); `db/tests/run.sh` rollback pin `72` → `73`.
  Columns: `runtime text NOT NULL DEFAULT 'claude_code'` (constraint `agent_sessions_cc_runtime`, values `openai`,
  `claude_code`), `agent_config jsonb` (constraint `agent_sessions_cc_agent_config`: NULL or an object),
  `history jsonb NOT NULL DEFAULT '[]'` (constraint `agent_sessions_cc_history`: an array), and
  `agent_sessions_cc_native_config CHECK ((runtime = 'openai') = (agent_config IS NOT NULL))`.
- Run keys stay `cc:<session id>:<turn>` for both types (`session_worker.run_key`). The `start` event's `kind` is
  `claude_code` for Claude Code turns (unchanged) and `native_session` for native turns.
- Texts (API, 400):
  - `NO_AGENT = 'Select an enabled agent.'` (unknown, disabled or coordinator agent; replaces
    `Select an enabled Claude Code agent.`);
  - `AGENT_MODEL = 'The agent has no enabled model for its agent type. Choose one on its General tab.'` (no model, a
    disabled model, or one that does not fit the type: Claude Code needs a Claude connection, native a Responses or
    Claude connection);
  - `NO_FOLDER` unchanged, raised for Claude Code agents only; `NOTHING_CHOSEN` and the folder texts unchanged.
- Texts (worker status messages, also the `error` event): `TIMED_OUT = 'The turn reached its time limit of 5 minutes.
  Send a message to continue in smaller steps.'`; `TOO_MANY_STEPS = 'The turn reached its limit of 20 model calls. Send
  a message to continue in smaller steps.'`; `UNREADABLE = 'A configured credential could not be decrypted. Save it
  again in administration.'`; a `ValueError` (configuration problems, such as an unavailable folder, a disabled agent
  or model): its message, redacted, one line, at most 500 characters; anything else: `Agent execution failed
  (<ExceptionType>). Check the model connection and agent resources.`; Stop: `session_worker.STOPPED` (unchanged).
- Native turn limits: `TURN_TIMEOUT_SECONDS = 300.0` and `MAX_TURNS = 20` (the defaults of `agents.agent()`, as for
  test chats). Concurrent native sessions per worker: `AGENT_SESSION_MAX_SESSIONS` (default 4).
- Native event payloads: `tool_call` `{'name', 'call_id', 'arguments' (parsed JSON, or the raw string), 'summary'}`;
  `tool_result` `{'name', 'call_id', 'output' (redacted, capped at 64 KiB), 'preview' (first 20 lines, capped at 2000
  characters), 'is_error'}`; `message` `{'text'}`; `reasoning` `{'text'}`; `instructions` `{'agent', 'text'}` (first
  turn only); one `usage` per turn `{'input_tokens', 'output_tokens', 'cached_tokens', 'total_tokens', 'cost_usd': 0.0}`
  (sums of the turn's model calls; `input_tokens` includes cached tokens, as Claude Code's).
- Summaries: `read_file`/`list_files` → `path`; `search_files` → `<query> in <path>`; `code_search` → `<name> in
  <path>`; `ast_grep` → `<pattern or rule> in <path>`; `shell_run` → `command`; `ssh_run` → `<server_id>: <command>`;
  `search_documents` → `query`; `read_document` → `<collection>/<path>`; `code_graph` → `<action> <query or node_id>`
  (stripped); anything else (MCP tools) → `''`.
- UI texts: agent options `<name> (Native)` / `<name> (Claude Code)`; `<name> has no model. Choose one on its General
  tab.` (a preselected agent without a model); `Create one in Agents → New agent and choose its model.` (no agent);
  session list model line `<model> · <Native|Claude Code>`; session page `Waiting for the agent session worker. The
  session starts when the worker is running.` (native) and the unchanged Claude Code text; Agent runs type
  `native_session` → **Native session**.

## Review Focus

1. **A worker runs a session of the other type, or the Claude Code worker regresses.** Examples: `claim` without the
   runtime filter, so `claude-code-worker` tries to start the CLI for a native session (or the native worker runs a
   Claude Code session without isolation); leases, queued-message delivery, Stop, idle release, slot reclaim or the
   interrupted-turn recovery behave differently after the extraction. Pinned in:
   - Task 2: `test_each_worker_claims_only_sessions_of_its_agent_type`;
   - Task 2: the unchanged `test_claude_code_runner.py` and `test_claude_code_worker.py` suites;
   - Task 6: `test_both_workers_run_only_their_own_sessions`.
2. **A credential reaches the database, a log or an API response.** Examples: the decrypted model key in
   `agent_config`, in a tool result, in the draft, in a status message or in a history item; `agent_config` or `history`
   returned by the session API; a key cut in half by the tool-output cap. Pinned in:
   - Task 3: `test_native_agents_start_sessions_with_their_config_and_the_chosen_folders_as_code_roots`;
   - Task 4: `test_commit_appends_scrubbed_items_in_the_callers_transaction_and_drop_discards`;
   - Task 5: `test_tool_output_is_redacted_before_it_is_capped`;
   - Task 6: `test_no_credential_reaches_events_history_the_draft_or_the_status`.
3. **History is left partial or committed apart from its turn.** Examples: items added by a failed, stopped or
   timed-out turn saved; history written by the SDK during a streamed run and then cut by `run.cancel()`; history
   committed although the runner lost its lease; the draft and the history of a turn in different transactions.
   Pinned in:
   - Task 2: `test_finish_turn_commits_with_a_successful_turn_only`;
   - Task 4: `test_agent_reads_and_buffers_the_conversation_through_the_sdk_session`;
   - Task 6: `test_failed_stopped_and_timed_out_turns_keep_the_history_and_draft`.
4. **A native session gets more access than the request describes, or the form overrides the agent.** Examples: code
   roots beyond the chosen code projects, the linked code projects and the agent's own; a switched-off agent's saved
   code projects switched on; a native agent with a Chat Completions model or the coordinator accepted; a different
   model than the agent's. Pinned in:
   - Task 3: `test_code_roots_add_the_session_folders_and_keep_a_switched_off_agents_projects_out`;
   - Task 3: `test_native_sessions_need_an_enabled_agent_whose_model_fits_its_type`;
   - Task 3: `test_native_agents_start_sessions_with_their_config_and_the_chosen_folders_as_code_roots`.
5. **The session page or Agent runs show native turns differently.** Examples: tool calls without a summary, tool
   results without a preview, usage written per model call, a different run key, missing `end` events, tokens not
   added to the session, an unlabelled run type, the form still filtering native agents out. Pinned in:
   - Task 5: `test_a_native_turn_becomes_the_events_of_a_claude_code_turn`;
   - Task 6: `test_native_turns_write_session_events_the_draft_tokens_and_history`;
   - Task 8: `Generate starts a session for a native agent`, `the session list shows each session's type`,
     `run types have readable names in the filter and the list`.

## Decisions (where the spec leaves a choice)

- **Same table, same routes, same page.** Native sessions live in `ka.agent_sessions_cc` with a `runtime` column and
  use `/api/claude-code/sessions*`. Everything the page, the stream, `events/history`, Save document, idle closing,
  deleting an agent with its history and Agent runs already do then works for native sessions without a second copy.
  The spec's rename to `/api/agent-sessions*` and `knowledge_agents/sessions.py` (and of the `ClaudeCodeSession*`
  components) is a pure rename; it is left for its own change so this one stays behavioural.
- **Run keys stay `cc:<session>:<turn>`** for both types, instead of the spec's `session:<id>:<turn>`: existing run
  records, the stream and history queries, and the agent-delete cleanup (`starts_with(run_key, 'cc:')`) keep working.
  The `start` event's `kind` (`claude_code` / `native_session`) tells the runtimes apart in Agent runs.
- **A separate worker, shared code.** `claude-code-worker` stays on its isolated `claude-code` network (Bash must not
  reach other services). Native turns need RAG, MCP servers, `shell-worker` and SSH hosts, so they run in
  `agent-session-worker` on `default` and `agent-shell`. Claiming, leases, queued input, Stop, finishing a turn, idle
  release and the serve loop move to `knowledge_agents/session_worker.py` and are used by both workers.
- **The native config is pinned when the session starts** (`agent_config`), like Claude Code's `prompt_snapshot` and
  like test chats' `ka.agent_sessions.config`: later edits or version activations do not change a running
  conversation. Credentials are decrypted per turn, so a disabled agent, model, server or MCP server stops the next
  turn (`load_credentials` checks them), as for test chats.
- **History lives in `agent_sessions_cc.history`**, not in `ka.agent_sessions` (the spec's table for test chats), because
  the session is the `agent_sessions_cc` row. `HistorySession` loads it once per turn; `commit` runs inside
  `finish_turn`'s transaction, only when the turn succeeded and this worker still owns the session.
- **No `SessionSettings(limit)`.** A limit keeps only the latest N items: it would drop the first message (what to
  document) and can separate a tool call from its output. Every turn sees the whole conversation;
  `HistorySession.session_settings` is `None`. Compaction for long sessions is a follow-up.
- **Session turns are not streamed** (`stream=False`). `agent()` cancels a streamed run right after its last event,
  which can cut the SDK's final session write; `Runner.run` returns after the SDK has added every item. The page still
  updates live: `SessionEvents` flushes after every model call and tool result, as the Claude Code worker flushes after
  every SDK message. `agent()` refuses `session=` together with `history` or `stream=True`.
- **Native sessions need no working folder** (`NO_FOLDER` is Claude Code's: its CLI needs a working directory). The
  same folder rules apply (exists, inside `DATA_ROOT`, no symbolic link), because the folders become code roots.
- **The instructions event is the real native prompt** (`agent.instructions` at `on_agent_start`, first turn only), so
  native turns write `start`, `input`, then `instructions`; `prompt_snapshot.system_prompt` holds the readable summary
  (prompt, instructions, skills) for both types.
- **Limits are the existing native ones** (300 s, 20 model calls per turn, as test chats); a turn that reaches one
  fails with its message, its items are dropped, and the next message continues from the last successful turn.
- **The form offers agents with a model only.** An agent preselected from its Test tab that has no model is named
  with `<name> has no model. Choose one on its General tab.` instead of being silently dropped.
- **Cost:** native turns record `cost_usd = 0` (the native runtime has no price data); tokens are recorded.

## Delivery and test boundaries

Tasks land in order on `feature/native-sessions`, and every suite is green after each task:
- Task 1 (migration) comes first: Tasks 2, 3 and 6 use the new columns.
- Task 2 (shared machinery) needs Task 1. Task 3 (API) needs Task 1. Tasks 4 (history) and 5 (events) are
  independent of each other; Task 4 needs Task 2 (`scrub`), Task 5 needs Tasks 2 and 3 (`native_sessions.py`).
- Task 6 (worker) needs Tasks 2–5. Task 7 (compose) needs Task 6 (the script). Task 8 (UI) needs Task 3 (`runtime`).
- Task 9 updates the docs and runs every suite.

---

### Task 1: Migration 0074 — sessions get an agent type, a pinned native config and native history

**Files:**
- Create: `db/migrations/0074_native_agent_sessions.up.sql`
- Create: `db/migrations/0074_native_agent_sessions.down.sql`
- Modify: `db/tests/run.sh` (rollback pin `72` → `73`)
- Create: `services/admin-api/tests/test_native_sessions_migration.py`

**Interfaces:**
- Produces: `ka.agent_sessions_cc.runtime`, `.agent_config`, `.history` with the constraints in Global Constraints.

- [ ] **Step 1: Write the failing test**

Create `services/admin-api/tests/test_native_sessions_migration.py`:

```python
"""0074: sessions get an agent type, a pinned native config and native history; Claude Code sessions are unchanged."""
from pathlib import Path

import psycopg
import pytest

from test_claude_code_sessions import sessions, start  # noqa: F401  (fixture)

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0074_native_agent_sessions.up.sql', '0074_native_agent_sessions.down.sql'


def run(conn, name: str):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def columns(conn) -> set[str]:
    return {row['column_name'] for row in conn.execute("""SELECT column_name FROM information_schema.columns
        WHERE table_schema='ka' AND table_name='agent_sessions_cc'""")}


def test_existing_sessions_are_claude_code_and_native_sessions_need_a_pinned_config(sessions):
    sid = start(sessions)['id']
    with sessions.store.connect() as conn:
        row = conn.execute('SELECT runtime,agent_config,history FROM ka.agent_sessions_cc WHERE id=%s', (sid,)).fetchone()
        assert row == {'runtime': 'claude_code', 'agent_config': None, 'history': []}
        for change in ("runtime='openai'", "agent_config='{}'", "history='{}'", "runtime='codex'",
                       "runtime='openai',agent_config='[]'"):
            with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
                conn.execute(f'UPDATE ka.agent_sessions_cc SET {change} WHERE id=%s', (sid,))
        conn.execute("""UPDATE ka.agent_sessions_cc SET runtime='openai',agent_config='{"id": "writer"}',
            history='[{"role": "user", "content": "Hi"}]' WHERE id=%s""", (sid,))


def test_down_refuses_native_sessions_then_drops_the_columns_and_up_restores_them(sessions):
    store = sessions.store
    sid = start(sessions)['id']
    with store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions_cc SET runtime='openai',agent_config='{}' WHERE id=%s", (sid,))
        conn.commit()
        with pytest.raises(psycopg.errors.RaiseException, match='Native agent sessions exist'):
            run(conn, DOWN)
        conn.rollback()
        conn.execute("UPDATE ka.agent_sessions_cc SET runtime='claude_code',agent_config=NULL WHERE id=%s", (sid,))
        conn.commit()
        run(conn, DOWN)
        conn.commit()
        assert not {'runtime', 'agent_config', 'history'} & columns(conn)
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_sessions_cc').fetchone()['n'] == 1  # Claude Code kept
        run(conn, UP)
        conn.commit()
        assert conn.execute('SELECT runtime,history FROM ka.agent_sessions_cc WHERE id=%s', (sid,)).fetchone() == {
            'runtime': 'claude_code', 'history': []}
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_native_sessions_migration.py -q`
Expected: FAIL: `psycopg.errors.UndefinedColumn: column "runtime" does not exist`.

- [ ] **Step 3: Implement**

Create `db/migrations/0074_native_agent_sessions.up.sql`:

```sql
BEGIN;
SET search_path = ka, public;

-- Generate documents sessions for both agent types (knowledge agents step 3). Claude Code sessions run in
-- claude-code-worker as before; native sessions run in agent-session-worker with the agent config pinned when the
-- session started and their Agents SDK conversation history.
ALTER TABLE agent_sessions_cc
    ADD COLUMN runtime text NOT NULL DEFAULT 'claude_code'
        CONSTRAINT agent_sessions_cc_runtime CHECK (runtime IN ('openai','claude_code')),
    ADD COLUMN agent_config jsonb
        CONSTRAINT agent_sessions_cc_agent_config CHECK (agent_config IS NULL OR jsonb_typeof(agent_config) = 'object'),
    ADD COLUMN history jsonb NOT NULL DEFAULT '[]'
        CONSTRAINT agent_sessions_cc_history CHECK (jsonb_typeof(history) = 'array'),
    ADD CONSTRAINT agent_sessions_cc_native_config CHECK ((runtime = 'openai') = (agent_config IS NOT NULL));
COMMENT ON COLUMN agent_sessions_cc.runtime IS 'The agent type the session runs with: openai (native) or claude_code.';
COMMENT ON COLUMN agent_sessions_cc.agent_config IS
    'Native sessions: the agent config pinned when the session started, with the session''s working folders as code roots. Credential references only.';
COMMENT ON COLUMN agent_sessions_cc.history IS
    'Native sessions: the Agents SDK items of successful turns, redacted.';

COMMIT;
```

Create `db/migrations/0074_native_agent_sessions.down.sql`:

```sql
BEGIN;
SET search_path = ka, public;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM agent_sessions_cc WHERE runtime = 'openai') THEN
        RAISE EXCEPTION 'Native agent sessions exist and 0073 cannot keep them; delete them (ka.agent_sessions_cc WHERE runtime = ''openai'', with their cc:<session>:* run records) before rolling back 0074';
    END IF;
END $$;

ALTER TABLE agent_sessions_cc
    DROP CONSTRAINT agent_sessions_cc_native_config,
    DROP COLUMN history,
    DROP COLUMN agent_config,
    DROP COLUMN runtime;

COMMIT;
```

`db/tests/run.sh`: change `[ "$rolled_back_version" = 72 ]` to `[ "$rolled_back_version" = 73 ]`.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_native_sessions_migration.py tests/test_claude_code_sessions.py tests/test_generate_documents_migration.py -q`
Expected: PASS.

Run: `bash db/tests/run.sh`
Expected: every `PASS:` line, exit 0 (it uses its own disposable containers).

- [ ] **Step 5: Commit**

```bash
git add db/migrations/0074_native_agent_sessions.up.sql db/migrations/0074_native_agent_sessions.down.sql db/tests/run.sh services/admin-api/tests/test_native_sessions_migration.py
git commit -m "feat(db): sessions get an agent type, a pinned native config and native history (0074)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Shared session machinery — claims per agent type, one SessionRunner base, one serve loop

**Files:**
- Create: `services/admin-api/src/knowledge_admin/knowledge_agents/session_worker.py`
- Modify: `services/admin-api/src/knowledge_admin/claude_code_worker.py`
- Create: `services/admin-api/tests/test_session_worker.py`

**Interfaces:**
- Consumes: Task 1 (`agent_sessions_cc.runtime`).
- Produces (`knowledge_admin.knowledge_agents.session_worker`): constants `PREVIEW_LINES`, `MAX_PREVIEW_CHARS`,
  `MAX_TOOL_OUTPUT`, `TOOL_TRUNCATED`, `LEASE_SECONDS`, `IDLE_RELEASE_SECONDS`, `STOPPED`, `INTERRUPTED`, `END_STATUS`,
  `CLOSED`, `LOST`; `run_key(session_id, turn) -> str`; `clean_text(text, secrets) -> str`; `capped(text, limit) -> str`;
  `scrub(value, secrets)`; `claim(store, worker_id, active, free, runtime) -> list[dict]`;
  `class SessionRunner(store, session, *, poll=1.0, idle_release=IDLE_RELEASE_SECONDS, slot=1)` with `run()`,
  `next_turn()`, `run_turn(number, text)` (abstract), `disconnect()`, `stop_requested()`,
  `finish_turn(number, status, message, *, draft, usage, claude_session_id=None, commit=None)`, `release()`;
  `serve(store, start, *, runtime, label, stopped, worker_id, limit, poll, reclaim=None)`.
- `claude_code_worker` keeps `claim(store, worker_id, active, free)` (Claude Code sessions), `serve(store,
  client_factory=sdk_client, *, stopped, worker_id=None, limit=None, poll=1.0, idle_release=…)`, `SessionRunner(store,
  session, client_factory=sdk_client, *, poll, idle_release, slot)` and the moved names (re-exported).

- [ ] **Step 1: Write the failing test**

Create `services/admin-api/tests/test_session_worker.py`:

```python
"""The session machinery both workers share: each claims only its agent type's sessions; a turn's commit runs in the
transaction that records it, only for a successful turn this worker still owns."""
from uuid import UUID

from psycopg.types.json import Jsonb

from knowledge_admin import claude_code_worker
from knowledge_admin.knowledge_agents import session_worker as shared
from test_claude_code_sessions import sessions, start  # noqa: F401  (fixture)


def test_each_worker_claims_only_sessions_of_its_agent_type(sessions):
    store = sessions.store
    claude, native = UUID(start(sessions)['id']), UUID(start(sessions)['id'])
    with store.connect() as conn:  # the second one as a native session (Task 3 creates them through the API)
        conn.execute("UPDATE ka.agent_sessions_cc SET runtime='openai',agent_config=%s WHERE id=%s",
                     (Jsonb({'id': sessions.agent}), native))
    assert [row['id'] for row in claude_code_worker.claim(store, 'cc-1', [], 4)] == [claude]
    [row] = shared.claim(store, 'native-1', [], 4, 'openai')
    assert (row['id'], row['worker_id'], row['runtime'], row['agent_name']) == (native, 'native-1', 'openai', 'Technical writer')
    assert shared.claim(store, 'native-2', [], 4, 'openai') == []  # leased by native-1
    assert shared.claim(store, 'cc-2', [], 4, 'claude_code') == []  # leased by cc-1
    assert shared.run_key(native, 3) == f'cc:{native}:3'


def test_finish_turn_commits_with_a_successful_turn_only(sessions):
    store = sessions.store
    sid = UUID(start(sessions)['id'])
    [row] = claude_code_worker.claim(store, 'worker-1', [], 4)
    runner = shared.SessionRunner(store, row)
    committed = []

    def commit(conn):  # sees the turn's own update: one transaction
        committed.append(conn.execute('SELECT status,draft FROM ka.agent_sessions_cc WHERE id=%s', (sid,)).fetchone())

    runner.finish_turn(1, 'failed', 'Broken.', draft='# Lost', usage=None, commit=commit)
    runner.finish_turn(2, 'stopped', shared.STOPPED, draft='# Lost', usage=None, commit=commit)
    assert committed == []
    runner.finish_turn(3, 'waiting', '', draft='# Kept', commit=commit,
                       usage={'input_tokens': 3, 'output_tokens': 2, 'cached_tokens': 1, 'cost_usd': 0.0})
    assert committed == [{'status': 'waiting', 'draft': '# Kept'}]
    with store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions_cc SET worker_id='other' WHERE id=%s", (sid,))
    runner.finish_turn(4, 'waiting', '', draft='# Not mine', usage=None, commit=commit)
    assert len(committed) == 1  # a runner that lost the session writes nothing
    with store.connect() as conn:
        final = conn.execute('SELECT draft,draft_turn,input_tokens,cached_tokens FROM ka.agent_sessions_cc WHERE id=%s',
                             (sid,)).fetchone()
    assert final == {'draft': '# Kept', 'draft_turn': 3, 'input_tokens': 3, 'cached_tokens': 1}


def test_claude_code_worker_keeps_its_names():
    for name in ('STOPPED', 'INTERRUPTED', 'MAX_TOOL_OUTPUT', 'MAX_PREVIEW_CHARS', 'TOOL_TRUNCATED', 'clean_text',
                 'LEASE_SECONDS', 'IDLE_RELEASE_SECONDS'):
        assert getattr(claude_code_worker, name) is getattr(shared, name), name
    assert issubclass(claude_code_worker.SessionRunner, shared.SessionRunner)
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_session_worker.py -q`
Expected: FAIL: `ModuleNotFoundError: No module named 'knowledge_admin.knowledge_agents.session_worker'`.

- [ ] **Step 3: Implement**

Create `services/admin-api/src/knowledge_admin/knowledge_agents/session_worker.py`:

```python
"""What the Claude Code and native session workers share: claiming the sessions of one agent type with a lease,
delivering queued messages as turns, Stop, finishing a turn (draft, tokens and, for native sessions, history in one
transaction), the idle release and the serve loop. A worker only adds how it runs a turn."""
import asyncio
import logging
import time
from collections.abc import Callable, Iterable
from contextlib import suppress
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from knowledge_admin.agent_activity import redact
from knowledge_admin.claude_code import close_idle
from knowledge_admin.repositories import RepositoryStore

logger = logging.getLogger(__name__)

PREVIEW_LINES = 20
MAX_PREVIEW_CHARS = 2000
MAX_TOOL_OUTPUT = 64 * 1024
TOOL_TRUNCATED = '… [truncated]'
LEASE_SECONDS = 60
IDLE_RELEASE_SECONDS = 300.0
STOPPED = 'Stopped. Send a message to continue.'
INTERRUPTED = 'The worker restarted during this turn. Send a message to continue.'
END_STATUS = {'waiting': 'completed', 'stopped': 'stopped', 'failed': 'failed'}
CLOSED = object()
LOST = object()  # another worker holds the session now: stop without further writes
NO_USAGE = {'input_tokens': 0, 'output_tokens': 0, 'cached_tokens': 0, 'cost_usd': 0.0}


def run_key(session_id, turn: int) -> str:
    """The run record of one session turn, for both agent types: the session page, its history, Activity → Agent runs
    and deleting an agent with its history read these keys."""
    return f'cc:{session_id}:{turn}'


def clean_text(text: str, secrets: Iterable[str]) -> str:
    """Text to store: without U+0000 (Postgres rejects it in text and jsonb), removed first so it cannot split a key,
    then redacted."""
    return redact(text.replace('\x00', ''), [secret for secret in secrets if secret])


def capped(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + TOOL_TRUNCATED


def scrub(value: Any, secrets: list[str]) -> Any:
    """clean_text for every string (and string key) of a JSON-like value."""
    if isinstance(value, str):
        return clean_text(value, secrets)
    if isinstance(value, dict):
        return {scrub(key, secrets) if isinstance(key, str) else key: scrub(item, secrets) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [scrub(item, secrets) for item in value]
    return value


def claim(store: RepositoryStore, worker_id: str, active: list[UUID], free: int, runtime: str) -> list[dict]:
    """Close idle sessions, renew this worker's leases, then claim sessions of this agent type (`runtime`) that need a
    worker: a queued message, or a turn left `working` by a worker that stopped (that turn is recorded as failed)."""
    with store.connect() as conn:
        close_idle(conn)
        conn.execute("""UPDATE ka.agent_sessions_cc SET lease_expires_at=now()+%s*interval '1 second'
            WHERE worker_id=%s AND id=ANY(%s::uuid[]) AND status<>'closed'""", (LEASE_SECONDS, worker_id, active))
        if free <= 0:
            return []
        rows = conn.execute("""WITH picked AS (
                SELECT s.id,s.status AS previous FROM ka.agent_sessions_cc s
                WHERE s.runtime=%s AND s.status<>'closed' AND (s.lease_expires_at IS NULL OR s.lease_expires_at<now())
                  AND NOT s.id=ANY(%s::uuid[])
                  AND (s.status='working' OR EXISTS (SELECT 1 FROM ka.agent_session_inputs i
                       WHERE i.session_id=s.id AND i.status='queued'))
                ORDER BY s.created_at,s.id LIMIT %s FOR UPDATE OF s SKIP LOCKED)
            UPDATE ka.agent_sessions_cc s SET worker_id=%s,lease_expires_at=now()+%s*interval '1 second',
                status=CASE WHEN s.status='working' THEN 'failed' ELSE s.status END,
                status_message=CASE WHEN s.status='working' THEN %s ELSE s.status_message END,
                stop_requested_at=CASE WHEN s.status='working' THEN NULL ELSE s.stop_requested_at END
            FROM picked WHERE s.id=picked.id
            RETURNING s.*,picked.previous,(SELECT a.name FROM ka.agents a WHERE a.id=s.agent_id) AS agent_name,
                EXISTS (SELECT 1 FROM ka.agent_session_inputs i WHERE i.session_id=s.id AND i.status='queued') AS queued""",
            (runtime, active, free, worker_id, LEASE_SECONDS, INTERRUPTED)).fetchall()
        for row in rows:
            if row['previous'] == 'working' and not row['queued']:  # failed now; it needs a worker only for a message
                conn.execute('UPDATE ka.agent_sessions_cc SET worker_id=NULL,lease_expires_at=NULL WHERE id=%s AND worker_id=%s',
                             (row['id'], worker_id))
            if row['previous'] == 'working' and row['turns'] > 0:
                key = run_key(row['id'], row['turns'])
                for kind, payload in (('error', {'message': INTERRUPTED}), ('end', {'status': 'failed'})):
                    conn.execute("""INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,actor_id,payload)
                        SELECT %s,%s,coalesce(max(seq),0)+1,%s,%s,%s,%s FROM ka.agent_run_events WHERE run_key=%s""",
                        (row['project_id'], key, kind, row['agent_id'], row['actor_id'], Jsonb(payload), key))
    return rows


class SessionRunner:
    """One session in one asyncio task until the session closes, this worker loses it, or it has been idle for
    `idle_release` seconds. Subclasses implement run_turn (and disconnect, for what they keep between turns)."""

    def __init__(self, store: RepositoryStore, session: dict, *, poll: float = 1.0,
                 idle_release: float = IDLE_RELEASE_SECONDS, slot: int = 1):
        self.store, self.session = store, session
        self.id, self.worker_id, self.slot = session['id'], session['worker_id'], slot
        self.poll, self.idle_release = poll, idle_release
        self.secrets: list[str] = []

    async def run(self) -> None:
        idle_since = time.monotonic()
        try:
            while True:
                turn = await asyncio.to_thread(self.next_turn)
                if turn is CLOSED or turn is LOST:
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
        """Deliver every queued message as the next turn, once; CLOSED when the session is closed, LOST when this worker
        no longer holds its lease."""
        with self.store.connect() as conn:
            row = conn.execute('''SELECT status,turns,(worker_id=%s AND lease_expires_at>now()) AS owned
                FROM ka.agent_sessions_cc WHERE id=%s FOR UPDATE''', (self.worker_id, self.id)).fetchone()
            if row is None or row['status'] == 'closed':
                return CLOSED
            if not row['owned']:
                return LOST
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
        raise NotImplementedError

    async def disconnect(self) -> None:
        return None

    def stop_requested(self) -> bool:
        with self.store.connect() as conn:
            row = conn.execute('''SELECT status,stop_requested_at,(worker_id=%s AND lease_expires_at>now()) AS owned
                FROM ka.agent_sessions_cc WHERE id=%s''', (self.worker_id, self.id)).fetchone()
        return row is None or row['status'] == 'closed' or row['stop_requested_at'] is not None or not row['owned']

    def finish_turn(self, number: int, status: str, message: str, *, draft: str, usage: dict[str, Any] | None,
                    claude_session_id: str | None = None, commit: Callable[[Any], None] | None = None) -> None:
        """Record the turn's outcome. The draft (already cleaned) replaces the previous one only after a successful turn
        with text. `commit(conn)` runs in the same transaction, only for a successful turn this worker still owns."""
        usage = usage or NO_USAGE
        draft = draft if status == 'waiting' and draft.strip() else None
        with self.store.connect() as conn:
            updated = conn.execute("""UPDATE ka.agent_sessions_cc SET
                status=CASE WHEN status='closed' THEN status ELSE %(status)s END,
                status_message=CASE WHEN status='closed' THEN status_message ELSE %(message)s END,
                draft=coalesce(%(draft)s::text, draft),
                draft_turn=CASE WHEN %(draft)s::text IS NULL THEN draft_turn ELSE %(turn)s END,
                claude_session_id=coalesce(%(cli)s::text, claude_session_id),
                input_tokens=input_tokens+%(input)s, output_tokens=output_tokens+%(output)s,
                cached_tokens=cached_tokens+%(cached)s, cost_usd=cost_usd+%(cost)s::numeric,
                stop_requested_at=NULL, last_activity_at=now() WHERE id=%(id)s AND worker_id=%(worker)s RETURNING id""",
                {'status': status, 'message': message, 'draft': draft, 'turn': number, 'cli': claude_session_id,
                 'input': usage['input_tokens'], 'output': usage['output_tokens'], 'cached': usage['cached_tokens'],
                 'cost': usage['cost_usd'], 'id': self.id, 'worker': self.worker_id}).fetchone()
            if updated is not None and status == 'waiting' and commit is not None:
                commit(conn)

    def release(self) -> None:
        with self.store.connect() as conn:
            conn.execute('UPDATE ka.agent_sessions_cc SET worker_id=NULL,lease_expires_at=NULL WHERE id=%s AND worker_id=%s',
                         (self.id, self.worker_id))


async def serve(store: RepositoryStore, start: Callable[[dict, int], SessionRunner], *, runtime: str, label: str,
                stopped: asyncio.Event, worker_id: str, limit: int, poll: float,
                reclaim: Callable[[Any, int], None] | None = None) -> None:
    """Claim sessions of `runtime` and run one task per session until `stopped` is set; leases are renewed every poll.
    Each running session holds one of `limit` slots until its task ends; `start(session, slot)` makes its runner.
    `reclaim(session_id, slot)` clears a slot before it is used again (Claude Code: the slot user's processes and the
    session HOME); a slot it cannot clear stays out of use."""
    active: dict[UUID, asyncio.Task] = {}
    slots: dict[UUID, int] = {}
    free_slots = list(range(1, limit + 1))
    try:
        while not stopped.is_set():
            for session_id, task in list(active.items()):
                if task.done():
                    del active[session_id]
                    slot = slots.pop(session_id)
                    try:  # nothing of this session stays readable by the next session in this slot
                        if reclaim is not None:
                            await asyncio.to_thread(reclaim, session_id, slot)
                    except Exception as exc:
                        logger.warning('%s slot %s is kept out of use: it could not be cleared (%s)',
                                       label, slot, type(exc).__name__)
                    else:
                        free_slots.append(slot)
                    if not task.cancelled() and task.exception() is not None:
                        logger.warning('%s session %s ended with %s', label, session_id, type(task.exception()).__name__)
            try:
                claimed = await asyncio.to_thread(claim, store, worker_id, list(active), len(free_slots), runtime)
            except psycopg.Error as exc:
                logger.warning('%s worker database unavailable (%s); retrying', label, type(exc).__name__)
                claimed = []
            for session in claimed:
                if session['previous'] == 'working' and not session['queued']:
                    continue  # its interrupted turn is recorded; claim released it until a message arrives
                slots[session['id']] = free_slots.pop(0)
                active[session['id']] = asyncio.create_task(start(session, slots[session['id']]).run())
            with suppress(TimeoutError):
                await asyncio.wait_for(stopped.wait(), poll)
    finally:
        for task in active.values():
            task.cancel()
        await asyncio.gather(*active.values(), return_exceptions=True)
```

`services/admin-api/src/knowledge_admin/claude_code_worker.py`:

1. Replace the import block (from `import asyncio` to `from knowledge_admin.run_record import RunRecorder`) with:

```python
import asyncio
import base64
import logging
import os
import shutil
import signal
import stat
import socket
import time
from collections import deque
from collections.abc import Iterable
from contextlib import AsyncExitStack, suppress
from pathlib import Path
from typing import Any
from uuid import UUID

from cryptography.exceptions import InvalidTag
from knowledge_llm import RuntimeModel, claude_model, decode_model
from psycopg.conninfo import make_conninfo

from knowledge_admin import run_record
from knowledge_admin.claude_code import ALLOWED_TOOLS, DISALLOWED_TOOLS
from knowledge_admin import claude_code_cli as cli
from knowledge_admin.claude_code_cli import HOME_ROOT, MAX_SLOTS, SESSION_HOMES, SLOT_ENV
from knowledge_admin.knowledge_agents import session_worker as shared
from knowledge_admin.knowledge_agents.session_worker import (  # noqa: F401  (Claude Code names kept for callers)
    CLOSED, END_STATUS, IDLE_RELEASE_SECONDS, INTERRUPTED, LEASE_SECONDS, LOST, MAX_PREVIEW_CHARS, MAX_TOOL_OUTPUT,
    PREVIEW_LINES, STOPPED, TOOL_TRUNCATED, capped, clean_text, run_key, scrub as _scrub)
from knowledge_admin.repositories import RepositoryStore
from knowledge_admin.run_record import RunRecorder
```

2. Delete the constants `PREVIEW_LINES`, `MAX_PREVIEW_CHARS`, `MAX_TOOL_OUTPUT`, `TOOL_TRUNCATED` (lines 37–40) and
   `LEASE_SECONDS`, `IDLE_RELEASE_SECONDS`, `STOPPED`, `INTERRUPTED`, `END_STATUS`, `CLOSED`, `LOST` (lines 51–58); keep
   `BASE_ENV`, `HINTS`, `DEFAULT_HINT` and `INTERRUPT_GRACE_SECONDS`.
3. Delete the functions `clean_text`, `capped` and `_scrub` (now imported).
4. Replace the whole `claim` function with:

```python
def claim(store: RepositoryStore, worker_id: str, active: list[UUID], free: int) -> list[dict]:
    """Claude Code sessions only (session_worker.claim)."""
    return shared.claim(store, worker_id, active, free, 'claude_code')
```

5. Replace the `SessionRunner` class header, docstring, `__init__`, `run` and `next_turn` with:

```python
class SessionRunner(shared.SessionRunner):
    """One Claude Code session in one asyncio task: one client, created on the first turn and kept until the session
    closes or has been idle for `idle_release` seconds. This task connects, sends and interrupts; a child task reads
    responses. Claiming, queued messages, Stop and finishing a turn are shared (session_worker)."""

    def __init__(self, store: RepositoryStore, session: dict, client_factory=sdk_client, *, poll: float = 1.0,
                 idle_release: float = IDLE_RELEASE_SECONDS, slot: int = 1):
        super().__init__(store, session, poll=poll, idle_release=idle_release, slot=slot)
        self.client_factory = client_factory
        self.stack: AsyncExitStack | None = None
        self.client = None
        self.stderr: deque[str] = deque(maxlen=20)
```

   In `run_turn`, replace `recorder = RunRecorder(run_key=f'cc:{self.id}:{number}', project_id=…` with
   `recorder = RunRecorder(run_key=run_key(self.id, number), project_id=…` (rest of the call unchanged), and replace its
   last line `await asyncio.to_thread(self.finish_turn, number, status, message, turn)` with:

```python
        await asyncio.to_thread(self.finish_turn, number, status, message, draft=clean_text(turn.draft, self.secrets),
                                usage=turn.usage, claude_session_id=turn.session_id)
```

   Keep `consume`, `connect`, `load` and `disconnect` unchanged. Delete `stop_requested`, `finish_turn` and `release`
   (inherited).
6. Replace the whole `serve` function with:

```python
async def serve(store: RepositoryStore, client_factory=sdk_client, *, stopped: asyncio.Event, worker_id: str | None = None,
                limit: int | None = None, poll: float = 1.0, idle_release: float = IDLE_RELEASE_SECONDS) -> None:
    """Run Claude Code sessions (session_worker.serve). Each running session holds one slot (its own uid for the CLI);
    a later turn may run in any free slot. A slot is cleared (reclaim_slot) before it is used again."""
    worker_id = worker_id or f'{socket.gethostname()}:{os.getpid()}'
    limit = limit or int(os.environ.get('CLAUDE_CODE_MAX_SESSIONS', '4'))
    if limit > MAX_SLOTS:
        logger.warning('CLAUDE_CODE_MAX_SESSIONS is %s; this worker runs at most %s sessions', limit, MAX_SLOTS)
        limit = MAX_SLOTS
    await shared.serve(
        store, lambda session, slot: SessionRunner(store, session, client_factory, poll=poll, idle_release=idle_release,
                                                   slot=slot),
        runtime='claude_code', label='Claude Code', stopped=stopped, worker_id=worker_id, limit=limit, poll=poll,
        reclaim=lambda session_id, slot: reclaim_slot(session_id, slot))  # looked up per call: tests replace it
```

Check: `git grep -n "psycopg\.Error\|Jsonb\|close_idle\|redact(" -- services/admin-api/src/knowledge_admin/claude_code_worker.py`
prints nothing (those uses moved to `session_worker.py`; `from psycopg.conninfo import make_conninfo` stays).

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_session_worker.py tests/test_claude_code_worker.py tests/test_claude_code_runner.py tests/test_claude_code_sessions.py -q`
Expected: PASS (the Claude Code suites are unchanged).

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/knowledge_agents/session_worker.py services/admin-api/src/knowledge_admin/claude_code_worker.py services/admin-api/tests/test_session_worker.py
git commit -m "refactor(admin-api): share session claiming, turns, Stop and the serve loop between session workers

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Session API — Generate documents starts sessions for both agent types

**Files:**
- Create: `services/admin-api/src/knowledge_admin/knowledge_agents/native_sessions.py` (`code_roots` only)
- Modify: `services/admin-api/src/knowledge_admin/claude_code_api.py`
- Modify: `services/admin-api/tests/test_claude_code_sessions.py`

**Interfaces:**
- Consumes: Task 1; `knowledge_agents.config.model_fits(runtime, model)`.
- Produces: `native_sessions.code_roots(config, folders) -> dict`; `claude_code_api.NO_AGENT`, `AGENT_MODEL` (new
  text); `session_context(conn, project, value, agent_projects, *, needs_folder=True)`; session summary and detail
  include `runtime`; native rows have `agent_config` and `history = []`.

- [ ] **Step 1: Write the failing tests**

In `services/admin-api/tests/test_claude_code_sessions.py`:
- change the import line to
  `from knowledge_admin.claude_code_api import AGENT_MODEL, NO_AGENT, NO_FOLDER, NOTHING_CHOSEN`, and add
  `from knowledge_admin.knowledge_agents.native_sessions import code_roots` and
  `from test_profiles import wizard_payload`;
- in `test_a_removed_folder_or_disabled_agent_refuses_the_session`, change
  `assert refused(sessions) == 'Select an enabled Claude Code agent.'` to `assert refused(sessions) == NO_AGENT`;
- in `test_generate_documents_sends_one_message_and_opens_the_folders`, after the first `assert (session['status'], …)`
  add `assert session['runtime'] == 'claude_code' and 'agent_config' not in session and 'history' not in session`;
- add at the end:

```python
def native_agent(client, name: str = 'Native writer', **change) -> str:
    """Create a native agent as New agent → Agent type: Native does."""
    body = wizard_payload(name, description='', when_to_use='', system_prompt='You document systems.', runtime='openai')
    response = client.post('/api/agents/wizard', headers=HEADERS, json={**body, **change})
    assert response.status_code == 201, response.text
    return response.json()['id']


def test_code_roots_add_the_session_folders_and_keep_a_switched_off_agents_projects_out():
    config = {'name': 'Writer', 'code': {'enabled': False, 'all_projects': True, 'projects': [
        {'id': 'own', 'name': 'Own', 'description': 'Its own', 'folder_paths': ['/c/own']}]}}
    folders = [{'path': '/c/app', 'kind': 'code_project', 'name': 'App', 'code_project_id': 'app'},
               {'path': '/c/app-2', 'kind': 'code_project', 'name': 'App', 'code_project_id': 'app'},
               {'path': '/c/own', 'kind': 'code_project', 'name': 'Own', 'code_project_id': 'own'}]
    pinned = code_roots(config, folders)
    assert pinned['code'] == {'enabled': True, 'all_projects': False, 'projects': [
        {'id': 'app', 'name': 'App', 'description': 'Chosen for this session.', 'folder_paths': ['/c/app', '/c/app-2']},
        {'id': 'own', 'name': 'Own', 'description': 'Chosen for this session.', 'folder_paths': ['/c/own']}]}
    assert config['code']['enabled'] is False  # the agent's config is not changed
    switched_on = {**config, 'code': {**config['code'], 'enabled': True}}
    assert [(p['id'], p['folder_paths']) for p in code_roots(switched_on, folders)['code']['projects']] == [
        ('own', ['/c/own']), ('app', ['/c/app', '/c/app-2'])]  # its own folder is not added twice
    assert code_roots(config, []) == config  # no folders: Code projects stays off


def test_native_agents_start_sessions_with_their_config_and_the_chosen_folders_as_code_roots(sessions):
    client, data = sessions.client, sessions.data
    agent = native_agent(client, model_id=sessions.claude, repositories_enabled=True, repository_ids=[sessions.worker])
    session = start(sessions, agent_id=agent, environment_ids=[], service_ids=[])
    app, worker = f'{data}/code/app', f'{data}/code/worker'
    assert (session['runtime'], session['status'], session['agent_name'], session['model_name']) == (
        'openai', 'starting', 'Native writer', 'Claude')
    assert [f['path'] for f in session['context_snapshot']['folders']] == [app, worker]  # chosen, then its own
    assert set(session['prompt_snapshot']) == {'system_prompt', 'agent_version'}  # no Claude Code tools
    assert session['prompt_snapshot']['system_prompt'].startswith('You document systems.')
    assert session['inputs'][0]['text'].startswith(f'Document the following.\n\n## Code\n- Payments API — {app}')
    serialized = json.dumps(session)
    for hidden in ('agent_config', 'history', 'api_key_ref', 'bedrock-secret-token'):
        assert hidden not in serialized
    with sessions.store.connect() as conn:
        row = conn.execute('SELECT runtime,agent_config,history FROM ka.agent_sessions_cc WHERE id=%s',
                           (session['id'],)).fetchone()
    assert (row['runtime'], row['history']) == ('openai', [])
    pinned = row['agent_config']
    assert (pinned['id'], pinned['runtime'], pinned['model']['id']) == (agent, 'openai', sessions.claude)
    assert pinned['code']['enabled'] is True and [(p['name'], p['folder_paths']) for p in pinned['code']['projects']] == [
        ('Payments worker', [worker]), ('Payments API', [app])]
    assert pinned['model']['api_key_ref']['id'] == sessions.claude  # a reference: nothing is decrypted here
    assert 'bedrock-secret-token' not in json.dumps(pinned)
    listed = client.get('/api/claude-code/sessions', headers=HEADERS).json()['sessions']
    assert [(s['id'], s['runtime']) for s in listed] == [(session['id'], 'openai')]
    # A native agent needs no working folder: servers only, without code projects of its own.
    plain = native_agent(client, 'Server writer', model_id=sessions.gpt)
    servers_only = start(sessions, agent_id=plain, code_project_ids=[], environment_ids=[], service_ids=[])
    assert servers_only['context_snapshot']['folders'] == []
    with sessions.store.connect() as conn:
        code = conn.execute('SELECT agent_config FROM ka.agent_sessions_cc WHERE id=%s',
                            (servers_only['id'],)).fetchone()['agent_config']['code']
    assert (code['enabled'], code['projects']) == (False, [])


def test_native_sessions_need_an_enabled_agent_whose_model_fits_its_type(sessions):
    client = sessions.client
    agent = native_agent(client, model_id=sessions.gpt)
    chat = client.post('/api/llm/models', headers=HEADERS, json={
        'name': 'Chat', 'provider': 'openai', 'model': 'gpt-4o', 'api_format': 'chat_completions',
        'base_url': 'http://model.test/v1'}).json()['id']
    with sessions.store.connect() as conn:
        conn.execute('UPDATE ka.agents SET model_id=%s WHERE id=%s', (chat, agent))
    assert refused(sessions, agent_id=agent) == AGENT_MODEL  # Chat Completions does not fit native agents
    with sessions.store.connect() as conn:
        conn.execute('UPDATE ka.agents SET model_id=%s,enabled=false WHERE id=%s', (sessions.gpt, agent))
    assert refused(sessions, agent_id=agent) == NO_AGENT
    assert refused(sessions, agent_id='coordinator') == NO_AGENT
    claude_code = claude_code_agent(client, 'GPT writer', model_id=sessions.claude)
    with sessions.store.connect() as conn:
        conn.execute('UPDATE ka.agents SET model_id=%s WHERE id=%s', (sessions.gpt, claude_code))
    assert refused(sessions, agent_id=claude_code) == AGENT_MODEL  # Claude Code needs a Claude connection
    with sessions.store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_sessions_cc').fetchone()['n'] == 0
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_claude_code_sessions.py -q`
Expected: FAIL at import: `ImportError: cannot import name 'NO_AGENT'`.

- [ ] **Step 3: Implement**

Create `services/admin-api/src/knowledge_admin/knowledge_agents/native_sessions.py`:

```python
"""Native agents in Generate documents sessions: the session's working folders as the agent's code roots."""
import copy
from typing import Any


def code_roots(config: dict[str, Any], folders: list[dict[str, Any]]) -> dict[str, Any]:
    """The config a native session runs with: the agent's own config plus every working folder of the session that is
    not already one of its code project folders, as code roots grouped by code project. With such folders Code projects
    is on; the agent's own projects stay in only when its Code projects switch was on (a switched-off agent's saved
    selections stay out). Credentials stay references; nothing is decrypted here."""
    pinned = copy.deepcopy(config)
    own = pinned['code']['projects'] if pinned['code']['enabled'] else []
    known = {path for project in own for path in project['folder_paths']}
    added: dict[str, dict[str, Any]] = {}
    for folder in folders:
        if folder['path'] in known:
            continue
        known.add(folder['path'])
        project = added.setdefault(str(folder['code_project_id']), {
            'id': str(folder['code_project_id']), 'name': folder['name'], 'description': 'Chosen for this session.',
            'folder_paths': []})
        project['folder_paths'].append(folder['path'])
    if added:
        pinned['code'] = {**pinned['code'], 'enabled': True, 'all_projects': False, 'projects': [*own, *added.values()]}
    return pinned
```

`services/admin-api/src/knowledge_admin/claude_code_api.py`:
- module docstring: `"""Generate documents sessions for both agent types: create, list, detail, send, stop, close, the event stream
  and saving the draft as a document. Claude Code turns run in claude-code-worker, native turns in
  agent-session-worker."""`;
- remove `from knowledge_llm import claude_model`; change `from knowledge_admin.knowledge_agents.config import
  get_agent_config` to `from knowledge_admin.knowledge_agents.config import get_agent_config, model_fits`; add
  `from knowledge_admin.knowledge_agents.native_sessions import code_roots`;
- replace the `AGENT_MODEL` line with:

```python
NO_AGENT = 'Select an enabled agent.'
AGENT_MODEL = 'The agent has no enabled model for its agent type. Choose one on its General tab.'
```

- in `_select`, change `SELECT s.id,s.title,s.agent_id,` to `SELECT s.id,s.title,s.runtime,s.agent_id,`;
- `session_context`: the signature becomes
  `def session_context(conn, project: UUID, value: SessionInput, agent_projects: list[dict], *, needs_folder: bool = True) -> dict:`,
  its docstring gains the sentence `Claude Code needs at least one folder (its working directory); native agents do
  not.`, and `if not folders:` becomes `if not folders and needs_folder:`;
- in `create_session`, replace everything from the `agent = conn.execute(` line through the
  `INSERT INTO ka.agent_sessions_cc(…)` statement with:

```python
            agent = conn.execute('SELECT id,enabled,runtime,is_coordinator FROM ka.agents WHERE id=%s FOR SHARE',
                                 (value.agent_id,)).fetchone()
            if agent is None or agent['is_coordinator'] or not agent['enabled']:
                raise HTTPException(400, NO_AGENT)
            try:  # the agent's saved config: its active version, or its working settings before the first version
                config = get_agent_config(agent['id'], conninfo=db.conninfo)
            except (LookupError, ValueError) as exc:
                raise HTTPException(400, str(exc)) from None
            model_id = (config['model'] or {}).get('id')  # the agent's own model; the form has none
            model = conn.execute('SELECT id,provider,model,api_format,enabled FROM ka.llm_models WHERE id=%s FOR SHARE',
                                 (model_id,)).fetchone() if model_id else None
            if model is None or not model['enabled'] or not model_fits(agent['runtime'], model):
                raise HTTPException(400, AGENT_MODEL)
            claude_code = agent['runtime'] == 'claude_code'
            context = session_context(conn, project, value, config['code']['projects'] if config['code']['enabled'] else [],
                                      needs_folder=claude_code)
            snapshot = {'system_prompt': agent_system_prompt(config), 'agent_version': config.get('config_version')}
            if claude_code:
                snapshot['claude_code_tools'] = config['claude_code_tools']
            # Native turns run with this config (credential references only); the worker decrypts per turn.
            pinned = None if claude_code else Jsonb(jsonable_encoder(code_roots(config, context['folders'])))
            session_id = uuid4()
            conn.execute("""INSERT INTO ka.agent_sessions_cc(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,
                context_snapshot,document_name,folder,runtime,agent_config) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (session_id, project, agent['id'], actor, model['id'], value.document_name, Jsonb(jsonable_encoder(snapshot)),
                 Jsonb(context), value.document_name, value.folder, agent['runtime'], pinned))
```

  and change the audit call to
  `audit(conn, actor, 'claude_code.session.created', 'agent_session', {'agent_id': agent['id'], 'runtime': agent['runtime']}, session_id)`.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_claude_code_sessions.py tests/test_claude_code_runner.py tests/test_session_worker.py tests/test_generate_documents.py tests/test_delete_agent_history.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/knowledge_agents/native_sessions.py services/admin-api/src/knowledge_admin/claude_code_api.py services/admin-api/tests/test_claude_code_sessions.py
git commit -m "feat(admin-api): Generate documents starts sessions for native and Claude Code agents

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Native history — an Agents SDK Session backed by the session row; agent() takes a session and hooks

**Files:**
- Create: `services/admin-api/src/knowledge_admin/knowledge_agents/history.py`
- Modify: `services/admin-api/src/knowledge_admin/agents.py` (`agent`)
- Create: `services/admin-api/tests/test_native_history.py`

**Interfaces:**
- Consumes: Task 1 (`history`), Task 2 (`session_worker.scrub`).
- Produces: `HistorySession(session_id, saved)` implementing `agents.memory.Session` (`session_id`,
  `session_settings = None`, `get_items(limit=None)`, `add_items(items)`, `pop_item()`, `clear_session()`), plus
  `HistorySession.load(conn, session_id)`, `commit(conn, secrets)`, `drop()`, `saved`, `pending`;
  `agents.agent(…, session: Session | None = None, hooks: RunHooks | None = None)`.

- [ ] **Step 1: Write the failing tests**

Create `services/admin-api/tests/test_native_history.py`:

```python
"""Native session history through the Agents SDK Session interface: the SDK reads the saved history and buffers the
turn's items; only a committed successful turn reaches the session row."""
import asyncio
import json

import pytest
from agents import RunHooks
from agents.memory import Session
from fastapi.encoders import jsonable_encoder

from knowledge_admin.agents import agent
from knowledge_admin.knowledge_agents.history import HistorySession
from test_claude_code_sessions import sessions, start  # noqa: F401  (fixture)
from test_sdk_agent import config, responses_api  # noqa: F401  (fixtures)

SECRET = {'model-key': 'fixture-api-secret'}


def test_history_session_buffers_until_commit_and_never_changes_saved_items():
    saved = [{'role': 'user', 'content': 'First'}]
    history = HistorySession('s1', saved)
    added = [{'role': 'assistant', 'content': 'Hello'}, {'role': 'user', 'content': 'Next'}]

    async def scenario():
        assert await history.get_items() == saved
        await history.add_items(added)
        assert await history.get_items() == [*saved, *added]
        assert await history.get_items(limit=1) == [added[-1]]
        assert await history.get_items(limit=0) == []
        assert await history.pop_item() == added[-1]
        await history.clear_session()  # this turn's items only
        assert await history.get_items() == saved
        assert await history.pop_item() is None  # the saved history is never popped

    asyncio.run(scenario())
    assert isinstance(history, Session) and history.session_settings is None and history.session_id == 's1'
    assert saved == [{'role': 'user', 'content': 'First'}]  # the caller's list is not changed


def test_commit_appends_scrubbed_items_in_the_callers_transaction_and_drop_discards(sessions):
    store = sessions.store
    sid = start(sessions)['id']
    with store.connect() as conn:
        history = HistorySession.load(conn, sid)
    assert (history.session_id, history.saved, history.pending) == (sid, [], [])
    asyncio.run(history.add_items([{'role': 'user', 'content': 'key fixture-api-secret\x00 here'},
                                   {'type': 'function_call_output', 'call_id': 'c1', 'output': 'fixture-api-secret'}]))
    with store.connect() as conn:
        history.commit(conn, ['fixture-api-secret'])
        conn.rollback()  # the caller's transaction decides
    with store.connect() as conn:
        assert HistorySession.load(conn, sid).saved == []
    with store.connect() as conn:
        history.commit(conn, ['fixture-api-secret', ''])
    with store.connect() as conn:
        assert HistorySession.load(conn, sid).saved == [
            {'role': 'user', 'content': 'key [redacted] here'},
            {'type': 'function_call_output', 'call_id': 'c1', 'output': '[redacted]'}]
    history.drop()
    assert history.pending == []
    with store.connect() as conn:
        with pytest.raises(LookupError):
            HistorySession.load(conn, '00000000-0000-0000-0000-000000000000')


def test_agent_reads_and_buffers_the_conversation_through_the_sdk_session(config, responses_api):
    _, requests = responses_api
    calls = []

    class Hooks(RunHooks):
        async def on_llm_end(self, context, agent, response):
            calls.append(response.usage.total_tokens)

    first = HistorySession('s1', [])
    result = asyncio.run(agent(config, 'Remember the test word apricot.', credentials=SECRET, session=first,
                               hooks=Hooks(), stream=False))
    assert result.final_output == 'Configured agent completed the task.' and calls == [30]
    assert first.saved == []  # nothing is saved by the run itself
    turn = json.dumps(jsonable_encoder(first.pending))  # what commit() stores
    assert 'Remember the test word apricot.' in turn and 'Configured agent completed the task.' in turn
    second = HistorySession('s1', json.loads(turn))
    asyncio.run(agent(config, 'What word did I give you?', credentials=SECRET, session=second, stream=False))
    resumed = json.dumps(requests[1]['body'])
    assert 'Remember the test word apricot.' in resumed and 'What word did I give you?' in resumed
    assert 'apricot' not in json.dumps(jsonable_encoder(second.pending))  # only the new turn is buffered
    for invalid in ({'history': [{'role': 'user', 'content': 'Earlier'}], 'stream': False}, {'stream': True}):
        with pytest.raises(ValueError, match='without history and without streaming'):
            asyncio.run(agent(config, 'Again.', credentials=SECRET, session=HistorySession('s1', []), **invalid))
    assert len(requests) == 2  # refused before the SDK started
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_native_history.py -q`
Expected: FAIL: `ModuleNotFoundError: No module named 'knowledge_admin.knowledge_agents.history'`.

- [ ] **Step 3: Implement**

Create `services/admin-api/src/knowledge_admin/knowledge_agents/history.py`:

```python
"""Native session history through the Agents SDK `Session` interface, backed by ka.agent_sessions_cc.history.

A turn loads the saved history once (HistorySession.load). The SDK reads it with get_items() and gives the turn's items
to add_items(), which only buffers them. The worker commits the buffer, without U+0000 and redacted with the turn's
credentials, in the transaction that records a successful turn (SessionRunner.finish_turn), and drops it otherwise, so
a failed, stopped or timed-out turn never leaves partial items. SessionSettings(limit) is not used: every turn sees the
whole conversation, including the first message that says what to document."""
from typing import Any

from fastapi.encoders import jsonable_encoder
from psycopg.types.json import Jsonb

from knowledge_admin.knowledge_agents.session_worker import scrub


class HistorySession:
    """An Agents SDK Session (the agents.memory.Session protocol) for one turn of a native session."""

    session_settings = None

    def __init__(self, session_id, saved: list[Any]):
        self.session_id = str(session_id)
        self.saved = list(saved)
        self.pending: list[Any] = []

    @classmethod
    def load(cls, conn, session_id) -> 'HistorySession':
        row = conn.execute('SELECT history FROM ka.agent_sessions_cc WHERE id=%s', (session_id,)).fetchone()
        if row is None:
            raise LookupError('Session not found.')
        return cls(session_id, row['history'])

    async def get_items(self, limit: int | None = None) -> list[Any]:
        items = [*self.saved, *self.pending]
        if limit is None:
            return items
        return items[-limit:] if limit > 0 else []

    async def add_items(self, items: list[Any]) -> None:
        self.pending.extend(items)

    async def pop_item(self) -> Any | None:
        """The SDK rewinds only what this turn added; the saved history never changes during a turn."""
        return self.pending.pop() if self.pending else None

    async def clear_session(self) -> None:
        """Drops this turn's items only. A session's history ends with the session (Close session), not here."""
        self.pending.clear()

    def commit(self, conn, secrets) -> None:
        """Append this turn's items, scrubbed, to the saved history inside the caller's transaction."""
        if not self.pending:
            return
        items = scrub(jsonable_encoder(self.pending), [secret for secret in secrets if secret])
        conn.execute('UPDATE ka.agent_sessions_cc SET history=history || %s WHERE id=%s', (Jsonb(items), self.session_id))

    def drop(self) -> None:
        self.pending.clear()
```

`services/admin-api/src/knowledge_admin/agents.py`:
- the SDK import becomes
  `from agents import Agent, AgentOutputSchema, FunctionTool, ModelSettings, RunConfig, RunHooks, Runner, RunResult, RunResultStreaming, Session, trace`;
- the signature of `agent` ends with `max_output_tokens: int | None = None, stream: bool = True,
  session: Session | None = None, hooks: RunHooks | None = None) -> RunResult | RunResultStreaming:`;
- append to its docstring: `With session (an Agents SDK Session, such as a Generate documents session's
  HistorySession) the SDK reads the earlier conversation from it and adds this turn's items to it; the caller then
  passes no history and does not stream, so every item has been added when the run returns. hooks receive the SDK
  lifecycle too (a session's run events).`;
- after the existing limits check add:

```python
    if session is not None and (history or stream):
        raise ValueError('Run a session turn without history and without streaming.')
```

- replace `task_input = [*(history or []), {'role': 'user', 'content': task}]` with
  `task_input = task if session is not None else [*(history or []), {'role': 'user', 'content': task}]`;
- replace `hooks = CompositeHooks(activity, recorder) if recorder else activity` with
  `run_hooks = CompositeHooks(activity, recorder, hooks) if recorder or hooks else activity`;
- in both runner calls replace `hooks=hooks,` with `hooks=run_hooks,` and add `session=session` after
  `run_config=run_config` (`Runner.run_streamed(…, run_config=run_config, session=session)` and
  `Runner.run(…, run_config=run_config, session=session)`).

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_native_history.py tests/test_sdk_agent.py tests/test_model_adapter.py tests/test_agent_sessions.py tests/test_run_record.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/knowledge_agents/history.py services/admin-api/src/knowledge_admin/agents.py services/admin-api/tests/test_native_history.py
git commit -m "feat(admin-api): native session history through an Agents SDK Session committed with the turn

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Native turn events — the SDK lifecycle as the events of a Claude Code turn

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/agent_activity.py` (`tool_failed`, used by `SdkActivity.on_tool_end`)
- Modify: `services/admin-api/src/knowledge_admin/knowledge_agents/native_sessions.py`
- Create: `services/admin-api/tests/native_fakes.py`
- Create: `services/admin-api/tests/test_native_session_events.py`

**Interfaces:**
- Consumes: Task 2 (`clean_text`, `capped`, `PREVIEW_LINES`, `MAX_PREVIEW_CHARS`, `MAX_TOOL_OUTPUT`), Task 3 (module).
- Produces: `agent_activity.tool_failed(detail: str) -> bool`; `native_sessions.summary(name, arguments) -> str`;
  `native_sessions.SessionEvents(recorder, *, secrets, first)` (a `RunHooks`) with `usage`, `finish()`,
  `draft(final_output) -> str`; test fakes `native_fakes.Recorder`, `reasoning`, `message`, `call`, `response`.

- [ ] **Step 1: Write the failing tests**

Create `services/admin-api/tests/native_fakes.py`:

```python
"""Stand-ins for the Agents SDK objects a native turn's hooks receive (model responses, tool contexts), shaped like the
real ones (test_native_session_events checks the fields)."""
import json
from types import SimpleNamespace

from claude_code_fakes import Recorder as _Recorder


class Recorder(_Recorder):
    """Collects events like RunRecorder.add and counts flushes."""

    def __init__(self):
        super().__init__()
        self.flushes = 0

    def flush(self) -> None:
        self.flushes += 1


def reasoning(text: str):
    return SimpleNamespace(type='reasoning', summary=[SimpleNamespace(text=text)], content=[])


def message(text: str):
    return SimpleNamespace(type='message', content=[SimpleNamespace(text=text)])


def call(call_id: str, name: str, arguments: dict):
    return SimpleNamespace(type='function_call', call_id=call_id, name=name, arguments=json.dumps(arguments))


def response(*items, input_tokens: int = 0, output_tokens: int = 0, cached: int = 0):
    """A ModelResponse: its output items and its Usage."""
    return SimpleNamespace(output=list(items), usage=SimpleNamespace(
        input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=input_tokens + output_tokens,
        input_tokens_details=SimpleNamespace(cached_tokens=cached)))
```

Create `services/admin-api/tests/test_native_session_events.py`:

```python
"""A native turn's SDK lifecycle becomes the run events a Claude Code turn writes, so the session page and Agent runs
show both the same way."""
import asyncio
import dataclasses
from types import SimpleNamespace

from native_fakes import Recorder, call, message, reasoning, response
from knowledge_admin.agent_activity import tool_failed
from knowledge_admin.knowledge_agents import native_sessions
from knowledge_admin.knowledge_agents.session_worker import MAX_PREVIEW_CHARS, MAX_TOOL_OUTPUT, TOOL_TRUNCATED

WRITER = SimpleNamespace(name='Native writer', instructions='Document the code.')
SDK_ERROR = 'An error occurred while running the tool. Please try again. Error: The requested path does not exist.'


def tool_end(events, call_id: str, name: str, output: str):
    return events.on_tool_end(SimpleNamespace(tool_call_id=call_id), WRITER, SimpleNamespace(name=name), output)


def test_a_native_turn_becomes_the_events_of_a_claude_code_turn():
    recorder = Recorder()
    events = native_sessions.SessionEvents(recorder, secrets=[], first=True)
    lines = '\n'.join(f'line {n}' for n in range(30))

    async def scenario():
        await events.on_agent_start(None, WRITER)
        await events.on_llm_end(None, WRITER, response(
            reasoning('Look at the routes first.'), call('c1', 'read_file', {'path': '/work/data/code/app/main.py'}),
            call('c2', 'shell_run', {'command': 'rg -n retry /work/data/code/app'}),
            call('c3', 'list_files', {'path': '/work/data/code/missing'}), input_tokens=100, output_tokens=20, cached=60))
        await tool_end(events, 'c1', 'read_file', 'import app')
        await tool_end(events, 'c2', 'shell_run', lines)
        await tool_end(events, 'c3', 'list_files', SDK_ERROR)
        await events.on_agent_start(None, WRITER)  # instructions are written once per session
        await events.on_llm_end(None, WRITER, response(message('Draft below.'), message('   '), message('# Payments API'),
                                                       input_tokens=50, output_tokens=30))

    asyncio.run(scenario())
    events.finish()
    assert [kind for kind, _ in recorder.events] == ['instructions', 'reasoning', 'tool_call', 'tool_call', 'tool_call',
                                                     'tool_result', 'tool_result', 'tool_result', 'message', 'message', 'usage']
    assert recorder.events[0] == ('instructions', {'agent': 'Native writer', 'text': 'Document the code.'})
    assert recorder.events[1] == ('reasoning', {'text': 'Look at the routes first.'})
    calls = [payload for kind, payload in recorder.events if kind == 'tool_call']
    assert [(c['name'], c['call_id'], c['summary']) for c in calls] == [
        ('read_file', 'c1', '/work/data/code/app/main.py'), ('shell_run', 'c2', 'rg -n retry /work/data/code/app'),
        ('list_files', 'c3', '/work/data/code/missing')]
    assert calls[0]['arguments'] == {'path': '/work/data/code/app/main.py'}
    read, shell, failed = [payload for kind, payload in recorder.events if kind == 'tool_result']
    assert read == {'name': 'read_file', 'call_id': 'c1', 'output': 'import app', 'preview': 'import app', 'is_error': False}
    assert shell['output'] == lines and shell['preview'] == '\n'.join(f'line {n}' for n in range(20))
    assert (failed['is_error'], failed['output']) == (True, SDK_ERROR)
    assert [payload['text'] for kind, payload in recorder.events if kind == 'message'] == ['Draft below.', '# Payments API']
    assert recorder.events[-1] == ('usage', {'input_tokens': 150, 'output_tokens': 50, 'cached_tokens': 60,
                                             'total_tokens': 200, 'cost_usd': 0.0})
    assert recorder.flushes == 6  # after the first agent start, each model call and each tool result
    assert events.draft('# Payments API\n\nFinal.') == '# Payments API\n\nFinal.'
    assert events.draft('  ') == events.draft(None) == '# Payments API'  # no final answer: the last message
    empty = native_sessions.SessionEvents(Recorder(), secrets=[], first=False)
    empty.finish()
    assert empty.recorder.events == [] and empty.draft(None) == ''  # no model call: no usage event


def test_tool_output_is_redacted_before_it_is_capped():
    secret = 'sk-native-secret'
    recorder = Recorder()
    events = native_sessions.SessionEvents(recorder, secrets=[secret], first=False)
    asyncio.run(tool_end(events, 'c1', 'shell_run', 'y' * (MAX_TOOL_OUTPUT - 8) + secret + '\x00'))
    asyncio.run(tool_end(events, 'c2', 'shell_run', 'x' * 100_000))
    first, second = [payload for _, payload in recorder.events]
    assert 'sk-native' not in repr(first) and '\x00' not in repr(first)  # no partial key survives the cut
    assert second['output'] == 'x' * MAX_TOOL_OUTPUT + TOOL_TRUNCATED
    assert second['preview'] == 'x' * MAX_PREVIEW_CHARS + TOOL_TRUNCATED


def test_tool_summaries_and_failures():
    summary = native_sessions.summary
    assert summary('search_files', {'query': 'retry', 'path': '/c/app'}) == 'retry in /c/app'
    assert summary('code_search', {'name': 'Charge', 'path': '/c/app'}) == 'Charge in /c/app'
    assert summary('ast_grep', {'rule': 'id: x', 'path': '/c/app'}) == 'id: x in /c/app'
    assert summary('ssh_run', {'server_id': 'prod-1', 'command': 'uptime'}) == 'prod-1: uptime'
    assert summary('search_documents', {'query': 'refunds'}) == 'refunds'
    assert summary('read_document', {'collection': 'runbooks', 'path': 'pay.md'}) == 'runbooks/pay.md'
    assert summary('code_graph', {'action': 'overview'}) == 'overview'
    assert summary('Docs__lookup', {'q': 'x'}) == '' and summary('read_file', 'not json') == ''
    assert tool_failed('{"exit_code": 2}') and tool_failed('{"timed_out": true}') and tool_failed(SDK_ERROR)
    assert not tool_failed('{"exit_code": 0}') and not tool_failed('plain text')


def test_fakes_match_the_sdk_types():
    from agents.items import ModelResponse
    from agents.tool_context import ToolContext
    from agents.usage import Usage
    from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseReasoningItem
    assert {'output', 'usage'} <= {field.name for field in dataclasses.fields(ModelResponse)}
    assert {'input_tokens', 'output_tokens', 'total_tokens', 'input_tokens_details'} <= {
        field.name for field in dataclasses.fields(Usage)}
    assert 'tool_call_id' in {field.name for field in dataclasses.fields(ToolContext)}
    assert {'type', 'call_id', 'name', 'arguments'} <= set(ResponseFunctionToolCall.model_fields)
    assert {'type', 'content'} <= set(ResponseOutputMessage.model_fields)
    assert {'type', 'summary', 'content'} <= set(ResponseReasoningItem.model_fields)
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_native_session_events.py -q`
Expected: FAIL at import: `ImportError: cannot import name 'tool_failed'`.

- [ ] **Step 3: Implement**

`services/admin-api/src/knowledge_admin/agent_activity.py`: add after `redact`:

```python
def tool_failed(detail: str) -> bool:
    """A tool result reported as failed: the SDK's error text, or JSON with error, isError, timed_out or a non-zero
    exit_code."""
    failed = detail.startswith('An error occurred while running the tool')
    try:
        output = json.loads(detail)
    except (ValueError, TypeError):
        output = None
    if isinstance(output, dict):
        failed = failed or bool(output.get('error') or output.get('isError') or output.get('timed_out'))
        failed = failed or output.get('exit_code', 0) not in (0, None)
    return failed
```

and in `SdkActivity.on_tool_end` replace the lines from `failed = detail.startswith(…)` through
`failed = failed or output.get('exit_code', 0) not in (0, None)` with `failed = tool_failed(detail)`.

`services/admin-api/src/knowledge_admin/knowledge_agents/native_sessions.py`: the docstring becomes
`"""Native agents in Generate documents sessions: the session's working folders as the agent's code roots and a turn's
SDK lifecycle as the session's run events."""`, the imports become:

```python
import asyncio
import copy
import json
from typing import Any

from agents import RunHooks

from knowledge_admin.agent_activity import tool_failed
from knowledge_admin.knowledge_agents.session_worker import (MAX_PREVIEW_CHARS, MAX_TOOL_OUTPUT, PREVIEW_LINES, capped,
                                                             clean_text)
```

and append after `code_roots`:

```python
def summary(name: str, arguments: Any) -> str:
    """One line for the session page, like Claude Code's Read/Grep/Glob/Bash summaries."""
    if not isinstance(arguments, dict):
        return ''

    def value(key: str) -> str:
        return str(arguments.get(key) or '')
    if name in ('read_file', 'list_files'):
        return value('path')
    if name == 'search_files':
        return f"{value('query')} in {value('path')}"
    if name == 'code_search':
        return f"{value('name')} in {value('path')}"
    if name == 'ast_grep':
        return f"{value('pattern') or value('rule')} in {value('path')}"
    if name == 'shell_run':
        return value('command')
    if name == 'ssh_run':
        return f"{value('server_id')}: {value('command')}"
    if name == 'search_documents':
        return value('query')
    if name == 'read_document':
        return f"{value('collection')}/{value('path')}"
    if name == 'code_graph':
        return f"{value('action')} {value('query') or value('node_id')}".strip()
    return ''


def _arguments(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return raw


class SessionEvents(RunHooks):
    """A native turn's SDK lifecycle as the run events of a Claude Code turn: instructions (first turn), reasoning,
    message, tool_call with a summary, tool_result with a preview, and one usage event for the turn (finish). Every
    hook flushes, so the session page follows the turn live. The recorder redacts every event with `secrets`; tool
    output is also cleaned before it is capped, so no partial key survives the cut."""

    def __init__(self, recorder, *, secrets: list[str], first: bool):
        self.recorder, self.secrets, self.first = recorder, secrets, first
        self.texts: list[str] = []
        self.calls = 0
        self.usage = {'input_tokens': 0, 'output_tokens': 0, 'cached_tokens': 0, 'total_tokens': 0, 'cost_usd': 0.0}

    async def _flush(self) -> None:
        await asyncio.to_thread(self.recorder.flush)

    async def on_agent_start(self, context, agent) -> None:
        if not self.first:
            return
        self.first = False
        text = agent.instructions if isinstance(agent.instructions, str) else '(generated at run time)'
        self.recorder.add('instructions', {'agent': agent.name, 'text': text})
        await self._flush()

    async def on_llm_end(self, context, agent, response) -> None:
        for item in response.output:
            kind = getattr(item, 'type', None)
            if kind == 'reasoning':
                parts = [part.text for part in (getattr(item, 'summary', None) or [])]
                parts += [part.text for part in (getattr(item, 'content', None) or [])]
                if any(part.strip() for part in parts):
                    self.recorder.add('reasoning', {'text': '\n'.join(parts)})
            elif kind == 'message':
                text = ''.join(getattr(part, 'text', '') for part in item.content)
                if text.strip():
                    self.texts.append(text)
                    self.recorder.add('message', {'text': text})
            elif kind == 'function_call':
                arguments = _arguments(item.arguments)
                self.recorder.add('tool_call', {'name': item.name, 'call_id': item.call_id, 'arguments': arguments,
                                                'summary': summary(item.name, arguments)})
        usage = response.usage
        details = getattr(usage, 'input_tokens_details', None)
        self.usage['input_tokens'] += int(usage.input_tokens or 0)
        self.usage['output_tokens'] += int(usage.output_tokens or 0)
        self.usage['cached_tokens'] += int(getattr(details, 'cached_tokens', 0) or 0)
        self.usage['total_tokens'] += int(usage.total_tokens or 0)
        self.calls += 1
        await self._flush()

    async def on_tool_end(self, context, agent, tool, result) -> None:
        output = clean_text(str(result), self.secrets)
        self.recorder.add('tool_result', {
            'name': tool.name, 'call_id': getattr(context, 'tool_call_id', None) or '',
            'output': capped(output, MAX_TOOL_OUTPUT),
            'preview': capped('\n'.join(output.splitlines()[:PREVIEW_LINES]), MAX_PREVIEW_CHARS),
            'is_error': tool_failed(output)})
        await self._flush()

    def finish(self) -> None:
        """The turn's one usage event (sums of its model calls); none when no model call finished."""
        if self.calls:
            self.recorder.add('usage', dict(self.usage))

    def draft(self, final_output: Any) -> str:
        """The final answer, or the last message when the answer is empty."""
        final = '' if final_output is None else str(final_output)
        return final if final.strip() else (self.texts[-1] if self.texts else '')
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_native_session_events.py tests/test_claude_code_sessions.py tests/test_sdk_agent.py tests/test_agent_sessions.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/agent_activity.py services/admin-api/src/knowledge_admin/knowledge_agents/native_sessions.py services/admin-api/tests/native_fakes.py services/admin-api/tests/test_native_session_events.py
git commit -m "feat(admin-api): native turns write the session events of a Claude Code turn

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: agent-session-worker — native turns with the pinned config, credentials, history, Stop and limits

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/knowledge_agents/native_sessions.py`
- Modify: `services/admin-api/pyproject.toml` (script)
- Modify: `services/admin-api/tests/native_fakes.py` (`FakeAgent`, `llm`, `tool`)
- Create: `services/admin-api/tests/test_native_sessions.py`

**Interfaces:**
- Consumes: Tasks 2–5; `agents.agent`, `agents.load_credentials`; `agent_tracing.setup`; `run_record.configure`.
- Produces: `native_sessions.TURN_TIMEOUT_SECONDS`, `MAX_TURNS`, `TIMED_OUT`, `TOO_MANY_STEPS`, `UNREADABLE`,
  `failure(exc, secrets) -> str`, `NativeRunner(store, session, run_agent=agent, *, poll, idle_release, slot,
  timeout_seconds, max_turns)`, `serve(store, run_agent=agent, *, stopped, worker_id=None, limit=None, poll=1.0,
  idle_release=…)`, `main()`; script `knowledge-agent-session-worker`.

- [ ] **Step 1: Write the failing tests**

Append to `services/admin-api/tests/native_fakes.py`:

```python
class FakeAgent:
    """Stands in for knowledge_admin.agents.agent. Each call plays the next scripted turn like the real runtime: the
    SDK reads the session, adds the user item, runs the steps through the hooks, then adds the answer and returns, or
    raises the turn's error (after the user item was buffered). A turn listed in `gates` waits before it ends, until
    the gate opens or the turn is cancelled."""

    def __init__(self, turns: list[dict], gates: dict | None = None):
        self.turns, self.gates = turns, gates or {}
        self.calls: list[dict] = []

    async def __call__(self, config, task, *, credentials, session, hooks, timeout_seconds, max_turns, stream):
        index = len(self.calls)
        self.calls.append({'config': config, 'task': task, 'credentials': dict(credentials),
                           'history': await session.get_items(), 'timeout_seconds': timeout_seconds,
                           'max_turns': max_turns, 'stream': stream})
        script = self.turns[index]
        await session.add_items([{'role': 'user', 'content': task}])
        writer = SimpleNamespace(name=config['name'], instructions='Native prompt for ' + config['name'])
        await hooks.on_agent_start(None, writer)
        for step in script.get('steps', []):
            await step(hooks, writer)
        if index in self.gates:
            await self.gates[index].wait()
        if 'error' in script:
            raise script['error']
        await session.add_items([{'role': 'assistant', 'content': script['answer']}])
        return SimpleNamespace(final_output=script['answer'])


def llm(*items, **usage):
    async def step(hooks, writer):
        await hooks.on_llm_end(None, writer, response(*items, **usage))
    return step


def tool(call_id: str, name: str, output: str):
    async def step(hooks, writer):
        await hooks.on_tool_end(SimpleNamespace(tool_call_id=call_id), writer, SimpleNamespace(name=name), output)
    return step
```

Create `services/admin-api/tests/test_native_sessions.py`:

```python
"""agent-session-worker with a fake native runtime: turns, events, the draft, tokens, history, Stop, failures and
limits, credentials, and both workers side by side."""
import asyncio
import json
import tomllib
from pathlib import Path
from uuid import UUID

import pytest
from agents import MaxTurnsExceeded

from claude_code_fakes import FakeClient
from native_fakes import FakeAgent, call, llm, message, tool
from knowledge_admin import claude_code_worker, run_record
from knowledge_admin.knowledge_agents import native_sessions
from knowledge_admin.knowledge_agents import session_worker as shared
from test_claude_code_runner import done, events, post, session_row, wait_for
from test_claude_code_sessions import native_agent, sessions, start  # noqa: F401  (fixture)
from test_repositories import HEADERS

KEY = 'bedrock-secret-token'  # the Claude connection's key: the native writer's model


@pytest.fixture
def native(sessions, monkeypatch):
    """A native writer with the Claude model and the Payments worker as its own code project, and a session that
    documents the Payments API code project and the prod-1 server."""
    monkeypatch.setattr(run_record, '_store', sessions.store)
    monkeypatch.setattr(claude_code_worker, 'reclaim_slot', lambda session_id, slot: None)
    agent = native_agent(sessions.client, model_id=sessions.claude, repositories_enabled=True,
                         repository_ids=[sessions.worker])
    session = start(sessions, agent_id=agent, environment_ids=[], service_ids=[])
    return sessions, UUID(session['id'])


def claimed(store) -> dict:
    [row] = shared.claim(store, 'worker-1', [], 4, 'openai')
    return row


def test_native_turns_write_session_events_the_draft_tokens_and_history(native):
    sessions, sid = native
    store, url, data = sessions.store, f'/api/claude-code/sessions/{sid}', sessions.data
    fake = FakeAgent([
        {'steps': [llm(call('c1', 'read_file', {'path': f'{data}/code/app/main.py'}), input_tokens=100, output_tokens=10,
                       cached=40),
                   tool('c1', 'read_file', 'import app'),
                   llm(message('# Payments API'), input_tokens=120, output_tokens=30)],
         'answer': '# Payments API'},
        {'steps': [llm(message('# Payments API\n\nShort.'), input_tokens=50, output_tokens=8)],
         'answer': '# Payments API\n\nShort.'}])

    async def scenario():
        stopped = asyncio.Event()
        serving = asyncio.create_task(native_sessions.serve(store, fake, stopped=stopped, worker_id='native-1', limit=2,
                                                            poll=0.02))
        first = await wait_for(store, sid, lambda s: s['status'] == 'waiting' and s['turns'] == 1)
        assert (first['draft'], first['draft_turn'], first['worker_id']) == ('# Payments API', 1, 'native-1')
        await post(sessions, url + '/messages', {'text': 'Shorten it.'})
        await wait_for(store, sid, lambda s: s['status'] == 'waiting' and s['turns'] == 2)
        await post(sessions, url + '/close')
        await wait_for(store, sid, lambda s: s['worker_id'] is None)
        stopped.set()
        await asyncio.wait_for(serving, 5)

    asyncio.run(scenario())
    first, second = fake.calls
    assert first['task'].startswith('Document the following.') and second['task'] == 'Shorten it.'
    assert first['history'] == [] and second['history'] == [
        {'role': 'user', 'content': first['task']}, {'role': 'assistant', 'content': '# Payments API'}]
    assert (first['timeout_seconds'], first['max_turns'], first['stream']) == (300.0, 20, False)
    assert first['credentials'] == {sessions.claude: KEY}  # decrypted in the worker, for this turn
    code = first['config']['code']
    assert code['enabled'] and [p['folder_paths'] for p in code['projects']] == [[f'{data}/code/worker'], [f'{data}/code/app']]
    row = session_row(store, sid)
    assert (row['status'], row['draft'], row['draft_turn'], row['input_tokens'], row['output_tokens'], row['cached_tokens'],
            row['cost_usd']) == ('closed', '# Payments API\n\nShort.', 2, 270, 48, 40, 0)
    assert row['history'] == [*second['history'], {'role': 'user', 'content': 'Shorten it.'},
                              {'role': 'assistant', 'content': '# Payments API\n\nShort.'}]
    recorded = events(store, sid)
    turn1 = [e for e in recorded if e['run_key'] == f'cc:{sid}:1']
    assert [e['kind'] for e in turn1] == ['start', 'input', 'instructions', 'tool_call', 'tool_result', 'message',
                                          'usage', 'end']
    assert turn1[0]['payload'] == {'kind': 'native_session', 'title': 'Payments – API reference', 'agent': 'Native writer',
                                   'turn': 1}
    assert turn1[2]['payload']['text'] == 'Native prompt for Native writer'
    assert turn1[3]['payload']['summary'] == f'{data}/code/app/main.py' and turn1[4]['payload']['preview'] == 'import app'
    assert turn1[-2]['payload'] == {'input_tokens': 220, 'output_tokens': 40, 'cached_tokens': 40, 'total_tokens': 260,
                                    'cost_usd': 0.0}
    assert [e['kind'] for e in recorded if e['run_key'] == f'cc:{sid}:2'] == ['start', 'input', 'message', 'usage', 'end']
    runs = sessions.client.get('/api/agent-runs?kind=native_session', headers=HEADERS).json()['runs']
    assert [(r['run_key'], r['status'], r['agent'], r['total_tokens']) for r in runs] == [
        (f'cc:{sid}:2', 'completed', 'Native writer', 58), (f'cc:{sid}:1', 'completed', 'Native writer', 260)]
    page = sessions.client.get(url + '/events/history', headers=HEADERS).json()['events']
    assert [e['kind'] for e in page if e['turn'] == 1][3:5] == ['tool_call', 'tool_result']
    assert 'output' not in next(e['payload'] for e in page if e['kind'] == 'tool_result')  # the page gets the preview


def test_failed_stopped_and_timed_out_turns_keep_the_history_and_draft(native):
    sessions, sid = native
    store, url = sessions.store, f'/api/claude-code/sessions/{sid}'
    fake = FakeAgent([
        {'steps': [llm(message('# Draft one'), input_tokens=10, output_tokens=5)], 'answer': '# Draft one'},
        {'steps': [llm(message('Reading more'), input_tokens=7, output_tokens=1)], 'error': TimeoutError()},
        {'steps': [], 'error': MaxTurnsExceeded('Max turns (20) exceeded')},
        {'steps': [llm(message('Partial'), input_tokens=3, output_tokens=1)], 'answer': 'never reached'},
    ], gates={3: asyncio.Event()})

    async def scenario():
        runner = asyncio.create_task(native_sessions.NativeRunner(store, claimed(store), fake, poll=0.02).run())
        await wait_for(store, sid, lambda s: s['status'] == 'waiting' and s['turns'] == 1)
        await post(sessions, url + '/messages', {'text': 'Add the error codes.'})
        timed_out = await wait_for(store, sid, lambda s: s['status'] == 'failed' and s['turns'] == 2)
        await post(sessions, url + '/messages', {'text': 'Try again.'})
        too_long = await wait_for(store, sid, lambda s: s['status'] == 'failed' and s['turns'] == 3)
        await post(sessions, url + '/messages', {'text': 'Once more.'})
        await wait_for(store, sid, lambda s: s['status'] == 'working' and s['turns'] == 4)
        await asyncio.sleep(0.1)
        assert (await post(sessions, url + '/stop')).status_code == 202
        stopped = await wait_for(store, sid, lambda s: s['status'] == 'stopped')
        await post(sessions, url + '/close')
        await asyncio.wait_for(runner, 5)
        return timed_out, too_long, stopped

    timed_out, too_long, stopped = asyncio.run(scenario())
    assert timed_out['status_message'] == native_sessions.TIMED_OUT
    assert too_long['status_message'] == native_sessions.TOO_MANY_STEPS
    assert stopped['status_message'] == shared.STOPPED
    row = session_row(store, sid)
    assert (row['draft'], row['draft_turn'], row['input_tokens'], row['output_tokens']) == ('# Draft one', 1, 20, 7)
    turn1 = [{'role': 'user', 'content': fake.calls[0]['task']}, {'role': 'assistant', 'content': '# Draft one'}]
    assert row['history'] == turn1  # the later turns' buffered items were dropped
    assert [c['history'] for c in fake.calls[1:]] == [turn1, turn1, turn1]
    recorded = events(store, sid)
    assert [e['payload']['status'] for e in recorded if e['kind'] == 'end'] == ['completed', 'failed', 'failed', 'stopped']
    assert [e['payload']['message'] for e in recorded if e['kind'] == 'error'] == [
        native_sessions.TIMED_OUT, native_sessions.TOO_MANY_STEPS]


def test_no_credential_reaches_events_history_the_draft_or_the_status(native, caplog):
    caplog.set_level('DEBUG')
    sessions, sid = native
    store, url = sessions.store, f'/api/claude-code/sessions/{sid}'
    fake = FakeAgent([
        {'steps': [llm(call('c1', 'shell_run', {'command': f'echo {KEY}'})),
                   tool('c1', 'shell_run', f'{KEY}\x00 printed'), llm(message(f'Key {KEY}'))],
         'answer': f'# Keys {KEY}'},
        {'steps': [], 'error': ValueError(f'Agent folder is unavailable: /work/{KEY}')},
        {'steps': [], 'error': RuntimeError(f'provider said {KEY}')}])

    async def scenario():
        runner = asyncio.create_task(native_sessions.NativeRunner(store, claimed(store), fake, poll=0.02).run())
        await wait_for(store, sid, lambda s: s['status'] == 'waiting' and s['turns'] == 1)
        await post(sessions, url + '/messages', {'text': 'Again'})
        config_error = await wait_for(store, sid, lambda s: s['status'] == 'failed' and s['turns'] == 2)
        await post(sessions, url + '/messages', {'text': 'Once more'})
        other = await wait_for(store, sid, lambda s: s['status'] == 'failed' and s['turns'] == 3)
        await post(sessions, url + '/close')
        await asyncio.wait_for(runner, 5)
        return config_error, other

    config_error, other = asyncio.run(scenario())
    row = session_row(store, sid)
    stored = json.dumps(events(store, sid)) + json.dumps([row, config_error, other], default=str) + caplog.text
    assert KEY not in stored and '\\u0000' not in stored
    assert row['draft'] == '# Keys [redacted]' and row['history'][-1] == {'role': 'assistant', 'content': '# Keys [redacted]'}
    assert config_error['status_message'] == 'Agent folder is unavailable: /work/[redacted]'
    assert other['status_message'] == ('Agent execution failed (RuntimeError). Check the model connection and agent '
                                       'resources.')
    [result] = [e['payload'] for e in events(store, sid) if e['kind'] == 'tool_result']
    assert result['output'] == '[redacted] printed'


def test_a_disabled_model_fails_the_turn_before_the_runtime_starts(native):
    sessions, sid = native
    store = sessions.store
    with store.connect() as conn:
        conn.execute('UPDATE ka.llm_models SET enabled=false WHERE id=%s', (sessions.claude,))
    fake = FakeAgent([])

    async def scenario():
        runner = asyncio.create_task(native_sessions.NativeRunner(store, claimed(store), fake, poll=0.02).run())
        failed = await wait_for(store, sid, lambda s: s['status'] == 'failed')
        await post(sessions, f'/api/claude-code/sessions/{sid}/close')
        await asyncio.wait_for(runner, 5)
        return failed

    failed = asyncio.run(scenario())
    assert failed['status_message'] == 'The configured model is unavailable or disabled.' and fake.calls == []


def test_both_workers_run_only_their_own_sessions(native):
    sessions, native_id = native
    store = sessions.store
    claude_id = UUID(start(sessions)['id'])
    client = FakeClient([[done('# Claude draft')]])
    fake = FakeAgent([{'steps': [llm(message('# Native draft'))], 'answer': '# Native draft'}])

    async def scenario():
        stopped = asyncio.Event()
        workers = [
            asyncio.create_task(claude_code_worker.serve(store, client, stopped=stopped, worker_id='cc-1', limit=2, poll=0.02)),
            asyncio.create_task(native_sessions.serve(store, fake, stopped=stopped, worker_id='native-1', limit=2, poll=0.02))]
        await wait_for(store, claude_id, lambda s: s['status'] == 'waiting')
        await wait_for(store, native_id, lambda s: s['status'] == 'waiting')
        stopped.set()
        await asyncio.wait_for(asyncio.gather(*workers), 5)

    asyncio.run(scenario())
    assert (session_row(store, claude_id)['draft'], session_row(store, native_id)['draft']) == ('# Claude draft', '# Native draft')
    assert (len(client.queries), len(fake.calls)) == (1, 1)
    assert [e['payload']['kind'] for e in events(store, claude_id) if e['kind'] == 'start'] == ['claude_code']
    assert [e['payload']['kind'] for e in events(store, native_id) if e['kind'] == 'start'] == ['native_session']


def test_the_worker_script_is_installed():
    pyproject = Path(__file__).resolve().parents[1] / 'pyproject.toml'
    scripts = tomllib.loads(pyproject.read_text())['project']['scripts']
    assert scripts['knowledge-agent-session-worker'] == 'knowledge_admin.knowledge_agents.native_sessions:main'
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_native_sessions.py -q`
Expected: FAIL: `AttributeError: module 'knowledge_admin.knowledge_agents.native_sessions' has no attribute 'serve'`
(and `NativeRunner`, the script).

- [ ] **Step 3: Implement**

`services/admin-api/pyproject.toml`, `[project.scripts]`: add after `knowledge-claude-cli`:

```toml
knowledge-agent-session-worker = "knowledge_admin.knowledge_agents.native_sessions:main"
```

`services/admin-api/src/knowledge_admin/knowledge_agents/native_sessions.py`: the docstring becomes

```python
"""Native agents in Generate documents sessions: the session's working folders as the agent's code roots, a turn's SDK
lifecycle as the session's run events, and agent-session-worker, which runs native turns with the agent config pinned
when the session started through the native runtime (knowledge_admin.agents.agent) and the SDK Session HistorySession.
Credentials are decrypted here, per turn, and redacted from events, the draft, status messages and history."""
```

the imports become:

```python
import asyncio
import base64
import copy
import json
import logging
import os
import signal
import socket
from collections.abc import Iterable
from typing import Any

from agents import MaxTurnsExceeded, RunHooks
from cryptography.exceptions import InvalidTag

from knowledge_admin import agent_tracing, run_record
from knowledge_admin.agent_activity import tool_failed
from knowledge_admin.agents import agent, load_credentials
from knowledge_admin.knowledge_agents import session_worker as shared
from knowledge_admin.knowledge_agents.history import HistorySession
from knowledge_admin.knowledge_agents.session_worker import (END_STATUS, IDLE_RELEASE_SECONDS, MAX_PREVIEW_CHARS,
                                                             MAX_TOOL_OUTPUT, PREVIEW_LINES, STOPPED, capped, clean_text,
                                                             run_key)
from knowledge_admin.repositories import RepositoryStore
from knowledge_admin.run_record import RunRecorder

logger = logging.getLogger(__name__)

TURN_TIMEOUT_SECONDS = 300.0  # the native runtime's defaults (agents.agent), as for test chats
MAX_TURNS = 20
TIMED_OUT = 'The turn reached its time limit of 5 minutes. Send a message to continue in smaller steps.'
TOO_MANY_STEPS = 'The turn reached its limit of 20 model calls. Send a message to continue in smaller steps.'
UNREADABLE = 'A configured credential could not be decrypted. Save it again in administration.'
```

and append after `SessionEvents`:

```python
def failure(exc: BaseException, secrets: Iterable[str]) -> str:
    """A failed turn's message for people: one line, at most 500 characters, without credentials."""
    if isinstance(exc, TimeoutError):
        return TIMED_OUT
    if isinstance(exc, MaxTurnsExceeded):
        return TOO_MANY_STEPS
    if isinstance(exc, InvalidTag):
        return UNREADABLE
    if isinstance(exc, ValueError):  # configuration problems: a missing folder, a disabled agent, model or server
        reason = ' '.join(clean_text(str(exc), secrets).split())[:500]
        if reason:
            return reason
    return f'Agent execution failed ({type(exc).__name__}). Check the model connection and agent resources.'


async def _cancel(task: asyncio.Task) -> None:
    """Cancel a turn and wait until agent() has closed its model and MCP connections."""
    task.cancel()
    await asyncio.wait({task})
    if not task.cancelled():
        task.exception()  # retrieved: a stopped turn's error does not matter


class NativeRunner(shared.SessionRunner):
    """One native session in one asyncio task. Each turn loads the pinned config, decrypts its credentials, loads the
    history, runs the native runtime as a task (cancelled on Stop, Close or a lost lease), then records the outcome
    with the history commit in one transaction. Credentials are cleared after every turn."""

    def __init__(self, store: RepositoryStore, session: dict, run_agent=agent, *, poll: float = 1.0,
                 idle_release: float = IDLE_RELEASE_SECONDS, slot: int = 1,
                 timeout_seconds: float = TURN_TIMEOUT_SECONDS, max_turns: int = MAX_TURNS):
        super().__init__(store, session, poll=poll, idle_release=idle_release, slot=slot)
        self.run_agent, self.timeout_seconds, self.max_turns = run_agent, timeout_seconds, max_turns

    def load(self) -> tuple[dict, dict[str, str], HistorySession]:
        """The pinned config, its credentials (decrypted now, for this turn) and the saved history. load_credentials
        refuses a disabled agent, model, SSH server or MCP server."""
        with self.store.connect() as conn:
            config = conn.execute('SELECT agent_config FROM ka.agent_sessions_cc WHERE id=%s',
                                  (self.id,)).fetchone()['agent_config']
            history = HistorySession.load(conn, self.id)
        return config, load_credentials(self.store, config), history

    async def run_turn(self, number: int, text: str) -> None:
        recorder = RunRecorder(run_key=run_key(self.id, number), project_id=str(self.session['project_id']),
                               agent_id=self.session['agent_id'], actor_id=str(self.session['actor_id']), secrets=self.secrets)
        recorder.add('start', {'kind': 'native_session', 'title': self.session['title'], 'agent': self.session['agent_name'],
                               'turn': number})
        recorder.add('input', {'task': text})
        await asyncio.to_thread(recorder.flush)
        events = SessionEvents(recorder, secrets=self.secrets, first=number == 1)
        credentials: dict[str, str] = {}
        history: HistorySession | None = None
        status, message, final = 'failed', '', None
        try:
            try:
                config, credentials, history = await asyncio.to_thread(self.load)
                self.secrets[:] = list(dict.fromkeys(value for value in credentials.values() if value))
                status, final = await self.drive(config, text, credentials, history, events)
                message = STOPPED if status == 'stopped' else ''
            except Exception as exc:
                status, message = 'failed', failure(exc, self.secrets)
                recorder.add('error', {'type': type(exc).__name__, 'message': message})
                logger.warning('Agent session %s turn %s failed (%s)', self.id, number, type(exc).__name__)
            events.finish()
            recorder.add('end', {'status': END_STATUS[status]})
            await asyncio.to_thread(recorder.flush)
            commit = None if history is None else (lambda conn: history.commit(conn, self.secrets))
            await asyncio.to_thread(self.finish_turn, number, status, message, usage=events.usage, commit=commit,
                                    draft=clean_text(events.draft(final), self.secrets))
        finally:
            if history is not None:
                history.drop()
            credentials.clear()
            self.secrets.clear()

    async def drive(self, config: dict, text: str, credentials: dict[str, str], history: HistorySession,
                    events: SessionEvents) -> tuple[str, Any]:
        """Run the turn: ('waiting', final answer) or ('stopped', None). Stop, Close or a lost lease cancels it; a
        runner that is itself cancelled (the worker stops) cancels it too."""
        turn = asyncio.create_task(self.run_agent(
            config, text, credentials=credentials, session=history, hooks=events,
            timeout_seconds=self.timeout_seconds, max_turns=self.max_turns, stream=False))
        try:
            while not turn.done():
                await asyncio.wait({turn}, timeout=self.poll)
                if not turn.done() and await asyncio.to_thread(self.stop_requested):
                    await _cancel(turn)
                    return 'stopped', None
            return 'waiting', turn.result().final_output  # raises the turn's error
        finally:
            if not turn.done():
                await _cancel(turn)


async def serve(store: RepositoryStore, run_agent=agent, *, stopped: asyncio.Event, worker_id: str | None = None,
                limit: int | None = None, poll: float = 1.0, idle_release: float = IDLE_RELEASE_SECONDS) -> None:
    """Run native sessions (session_worker.serve); AGENT_SESSION_MAX_SESSIONS of them at once (default 4)."""
    worker_id = worker_id or f'{socket.gethostname()}:{os.getpid()}'
    limit = limit or int(os.environ.get('AGENT_SESSION_MAX_SESSIONS', '4'))
    await shared.serve(
        store, lambda session, slot: NativeRunner(store, session, run_agent, poll=poll, idle_release=idle_release, slot=slot),
        runtime='openai', label='Agent session', stopped=stopped, worker_id=worker_id, limit=limit, poll=poll)


def main() -> None:
    """agent-session-worker: PostgreSQL from the PG* environment, run records like admin-api, and Agents SDK tracing to
    Phoenix (DOCS_WIKI_PHOENIX_ENDPOINT) as for native runs in admin-api."""
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s %(message)s')
    store = RepositoryStore('', base64.urlsafe_b64decode(os.environ['CREDENTIAL_ENCRYPTION_KEY']), os.environ['AUTH_ISSUER_URL'])
    run_record.configure(store, None)
    agent_tracing.setup()

    async def run() -> None:
        stopped = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(signum, stopped.set)
        await serve(store, stopped=stopped)

    asyncio.run(run())
```

Notes for the implementer:
- `finish_turn` is the shared one (Task 2): the draft and the history commit apply only to a successful turn this
  worker still owns; the buffer is dropped in every case afterwards.
- `load_credentials` raises `ValueError('The configured model is unavailable or disabled.')` for a disabled model,
  which `failure` passes through (redacted).

Run: `cd services/admin-api && uv lock --check` — Expected: exit 0 (a script entry does not change the lock). If it
reports a change, run `uv lock` and add `services/admin-api/uv.lock` to the commit.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_native_sessions.py tests/test_native_session_events.py tests/test_native_history.py tests/test_session_worker.py tests/test_claude_code_runner.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/knowledge_agents/native_sessions.py services/admin-api/pyproject.toml services/admin-api/tests/native_fakes.py services/admin-api/tests/test_native_sessions.py
git commit -m "feat(admin-api): agent-session-worker runs native agents' Generate documents sessions

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Compose — the agent-session-worker service

**Files:**
- Modify: `deploy/compose.yaml`
- Modify: `tests/test_setup.py`

**Interfaces:**
- Consumes: Task 6 (`/services/admin-api/.venv/bin/knowledge-agent-session-worker`).
- Produces: compose service `agent-session-worker` (networks `default`, `agent-shell`; `DATA_ROOT` read-only;
  `agent-workspaces` at `/work/agent-workspaces`; no ports); env `AGENT_SESSION_MAX_SESSIONS` (default 4).

- [ ] **Step 1: Write the failing test**

`tests/test_setup.py`:
- in `test_compose_mounts_the_data_root_read_only_and_writer_folders_writable`, add `"agent-session-worker": []` to
  `writers` (after `"claude-code-worker": []`);
- add after `test_claude_code_worker_mounts_and_environment`:

```python
    def test_agent_session_worker_mounts_networks_and_environment(self) -> None:
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
            worker = config["services"]["agent-session-worker"]
            data = str(root / "data").replace("$", "$$")
            self.assertEqual(sorted((v["type"], v["source"], v["target"], bool(v.get("read_only"))) for v in worker["volumes"]),
                             [("bind", data, "/work/data", True), ("volume", "agent-workspaces", "/work/agent-workspaces", False)])
            self.assertEqual(worker["entrypoint"], ["/services/admin-api/.venv/bin/knowledge-agent-session-worker"])
            self.assertNotIn("ports", worker)
            # Like admin-api: RAG, PostgreSQL, MCP servers and model providers on default; shell-worker on agent-shell.
            self.assertEqual(set(worker["networks"]), {"default", "agent-shell"})
            self.assertEqual(set(config["services"]["claude-code-worker"]["networks"]), {"claude-code"})  # unchanged
            env = worker["environment"]
            self.assertEqual((env["AGENT_SHELL_URL"], env["AGENT_WORKSPACE_ROOT"], env["CODE_ROOT"], env["DATA_ROOT_DIR"],
                              env["DOCS_WIKI_RAG_URL"], env["DOCS_WIKI_PHOENIX_ENDPOINT"], env["AGENT_SESSION_MAX_SESSIONS"]),
                             ("http://shell-worker:8796", "/work/agent-workspaces", "/work/data/code", "/work/data",
                              "http://rag:8790", "http://phoenix:6006/v1/traces", "4"))
            for name in ("PGHOST", "PGPASSWORD", "CREDENTIAL_ENCRYPTION_KEY", "AUTH_ISSUER_URL", "RAG_BACKOFFICE_KEY",
                         "DOCS_WIKI_PHOENIX_API_KEY"):
                self.assertIn(name, env)
            self.assertEqual(set(worker["depends_on"]), {"migrate", "rag", "shell-worker"})
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `python3 -m unittest discover -s tests -p test_setup.py`
Expected: FAIL: `KeyError: 'agent-session-worker'`.

- [ ] **Step 3: Implement**

`deploy/compose.yaml`: add after the `claude-code-worker` service (before `shell-worker:`):

```yaml
  agent-session-worker:
    build: {context: .., dockerfile: deploy/Dockerfile.admin-api}
    restart: unless-stopped
    # Native agents' Generate documents sessions. Like admin-api it reaches RAG, PostgreSQL, MCP servers, model
    # providers and SSH hosts on default and shell-worker on agent-shell; it has no host port.
    networks: [default, agent-shell]
    dns_search: ${DOCKER_DNS_SEARCH:-.}
    entrypoint: [/services/admin-api/.venv/bin/knowledge-agent-session-worker]
    command: []
    volumes:
      - *data-root
      - agent-workspaces:/work/agent-workspaces
    environment:
      <<: *data-env
      AGENT_SHELL_URL: http://shell-worker:8796
      AGENT_WORKSPACE_ROOT: /work/agent-workspaces
      AGENT_SESSION_MAX_SESSIONS: ${AGENT_SESSION_MAX_SESSIONS:-4}
      DOCS_WIKI_RAG_URL: http://rag:8790
      RAG_BACKOFFICE_KEY: ${RAG_BACKOFFICE_KEY:?Run make setup}
      DOCS_WIKI_PHOENIX_ENDPOINT: http://phoenix:6006/v1/traces
      DOCS_WIKI_PHOENIX_API_KEY: ${PHOENIX_ADMIN_SECRET:?Run make setup}
      PGHOST: postgres
      PGDATABASE: knowledge_agent
      PGUSER: knowledge_agent
      PGPASSWORD: ${POSTGRES_PASSWORD:?Run make setup}
      CREDENTIAL_ENCRYPTION_KEY: ${CREDENTIAL_ENCRYPTION_KEY:?Run make setup}
      AUTH_ISSUER_URL: http://${WEB_HOST:-localhost}:${KEYCLOAK_PORT:-8181}/realms/docs
    depends_on:
      migrate: {condition: service_completed_successfully}
      rag: {condition: service_healthy}
      shell-worker: {condition: service_healthy}

```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `python3 -m unittest discover -s tests -p test_setup.py`
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add deploy/compose.yaml tests/test_setup.py
git commit -m "feat(deploy): agent-session-worker service for native agent sessions

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: admin-ui — both agent types in Generate documents

**Files:**
- Modify: `services/admin-ui/app/lib/claudeCode.ts`
- Modify: `services/admin-ui/app/components/ClaudeCodeSessionForm.tsx`
- Modify: `services/admin-ui/app/components/ClaudeCodeSessionsView.tsx`
- Modify: `services/admin-ui/app/components/ClaudeCodeSessionView.tsx`
- Modify: `services/admin-ui/app/components/RunsView.tsx`
- Modify: `services/admin-ui/app/components/__tests__/claudeCodeSessionForm.test.tsx`
- Modify: `services/admin-ui/app/components/__tests__/claudeCodeSessions.test.tsx`
- Modify: `services/admin-ui/app/components/__tests__/claudeCodeSession.test.tsx`
- Modify: `services/admin-ui/app/components/__tests__/runs.test.tsx`

**Interfaces:**
- Consumes: Task 3 (`runtime` in session summary and detail; native agents accepted).
- Produces: `SessionSummary.runtime: AgentRuntime`; `sessionTypeLabel(runtime?)` (`'openai'` → `Native`, anything
  else → `Claude Code`: sessions read before 0074 have no `runtime` and are Claude Code).

- [ ] **Step 1: Write the failing tests**

`services/admin-ui/app/components/__tests__/claudeCodeSessionForm.test.tsx`:
- in `bodies['/api/agents'].agents`, add `is_coordinator: false` to every agent and append
  `{ id: 'coordinator', name: 'Coordinator', runtime: 'openai', enabled: true, is_coordinator: true, model_id: 'gpt', model_name: 'GPT' }`;
- in the first test replace the three lines from `expect(await screen.findByRole('option', { name: 'Technical writer' })).toBeVisible();`
  through `fireEvent.click(screen.getByRole('option', { name: 'Technical writer' }));` with:

```tsx
  expect(await screen.findByRole('option', { name: 'Technical writer (Claude Code)' })).toBeVisible();
  expect(screen.getByRole('option', { name: 'Search agent (Native)' })).toBeVisible();  // both agent types
  for (const hidden of [/Retired writer/, /Unfinished writer/, /Coordinator/]) expect(screen.queryByRole('option', { name: hidden })).toBeNull();
  fireEvent.click(screen.getByRole('option', { name: 'Technical writer (Claude Code)' }));
```

- in the second test, `await select('Agent', 'Technical writer');` becomes `await select('Agent', 'Technical writer (Claude Code)');`;
- replace the test `the Test tab link preselects the agent; an agent without a model cannot generate` with:

```tsx
test('the Test tab link preselects the agent; one without a model is named with the reason', async () => {
  setup(undefined, '/documentation/generations/new?agent=writer');
  expect(await screen.findByLabelText('Agent')).toHaveTextContent('Technical writer (Claude Code)');
});

test('an agent without a model is not offered; its Test tab link says why', async () => {
  setup(undefined, '/documentation/generations/new?agent=no-model');
  expect(await screen.findByText('Unfinished writer has no model. Choose one on its General tab.')).toBeVisible();
  expect(screen.getByLabelText('Agent')).toHaveTextContent('Select…');
  fireEvent.change(screen.getByLabelText('Document name'), { target: { value: 'Payments' } });
  check('Code'); check('Payments API');
  expect(screen.getByRole('button', { name: 'Generate' })).toBeDisabled();
  await select('Agent', 'API writer (Claude Code)');
  expect(screen.getByRole('button', { name: 'Generate' })).toBeEnabled();
});

test('Generate starts a session for a native agent', async () => {
  const posts = setup();
  await select('Agent', 'Search agent (Native)');
  expect(screen.getByText(/own model, prompt, instructions, skills and tools \(GPT\)/)).toBeVisible();
  fireEvent.change(screen.getByLabelText('Document name'), { target: { value: 'Hosts' } });
  check('Servers'); check('prod-1 (10.0.0.5)');
  fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
  expect(await screen.findByText('Session page')).toBeVisible();
  expect(posts).toEqual([{ agent_id: 'search', code_project_ids: [], environment_ids: [], service_ids: [], server_ids: ['srv'],
    instructions: '', document_name: 'Hosts', folder: 'docs' }]);
});
```

`services/admin-ui/app/components/__tests__/claudeCodeSessions.test.tsx`: add `runtime: 'claude_code'` to `row`;
in the fetch stub's sessions list add a third row
`{ ...row, id: 's3', title: 'Native overview', runtime: 'openai', model_name: 'GPT' }`; and add after the
`within(first).getByText('Technical writer')` assertion:

```tsx
  expect(within(first).getByText('Claude on Bedrock · Claude Code')).toBeVisible();
  const native = screen.getByRole('link', { name: 'Native overview' }).closest('tr')!;
  expect(within(native).getByText('GPT · Native')).toBeVisible();  // the session list shows each session's type
```

`services/admin-ui/app/components/__tests__/claudeCodeSession.test.tsx`: add at the end:

```tsx
test('a native session waits for the agent session worker', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.includes('/events/history') ? page([]) : Response.json({ ...detail, runtime: 'openai' })));
  render(<MemoryRouter><ClaudeCodeSessionView sessionId="s1" /></MemoryRouter>);
  expect(await screen.findByText('Waiting for the agent session worker. The session starts when the worker is running.')).toBeVisible();
  expect(screen.queryByText(/Claude Code worker/)).toBeNull();
});
```

`services/admin-ui/app/components/__tests__/runs.test.tsx`, test `run types have readable names in the filter and the
list`: the expected options become
`[['', 'All'], ['test', 'Agent test'], ['board', 'Board task'], ['claude_code', 'Claude Code'], ['native_session', 'Native session']]`.

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-ui && pnpm exec vitest run app/components/__tests__/claudeCodeSessionForm.test.tsx app/components/__tests__/claudeCodeSessions.test.tsx app/components/__tests__/claudeCodeSession.test.tsx app/components/__tests__/runs.test.tsx`
Expected: FAIL: no option `Technical writer (Claude Code)`; no `Claude on Bedrock · Claude Code`; no agent session
worker text; no `native_session` option.

- [ ] **Step 3: Implement**

`services/admin-ui/app/lib/claudeCode.ts`:
- add `import type { AgentRuntime } from './profiles';` under the existing imports;
- add after `formatTokens`:

```ts
/** A session's agent type; sessions read before migration 0074 have none and are Claude Code sessions. */
export const sessionTypeLabel = (runtime?: AgentRuntime) => runtime === 'openai' ? 'Native' : 'Claude Code';
```

- in `SessionSummary`, change `id: string; title: string; agent_id: string;` to
  `id: string; title: string; runtime?: AgentRuntime; agent_id: string;`.

`services/admin-ui/app/components/ClaudeCodeSessionForm.tsx`:
- import `agentTypeLabel` with `getAgents`: `import { agentTypeLabel, getAgents, type AgentSummary } from '~/lib/profiles';`;
- `type Data = { agents: AgentSummary[]; items: Record<Kind, Item[]>; folders: string[]; noModel: string };`
- in the effect, replace the `const claudeAgents = …` line, the `setData({ agents: claudeAgents, …` call's
  `agents: claudeAgents,` and the `if (!claudeAgents.some(…)) setAgentId('');` line with:

```tsx
        // Both agent types; only agents that can run: enabled, with a model, not the board's coordinator.
        const usable = agents.agents.filter(agent => agent.enabled && !agent.is_coordinator);
        const listed = usable.filter(agent => agent.model_id);
        const noModel = usable.find(agent => agent.id === initialAgentId && !agent.model_id)?.name ?? '';
```

  `setData({ agents: listed, noModel, folders: …` (rest unchanged), and
  `if (!listed.some(agent => agent.id === initialAgentId)) setAgentId('');`;
- the option becomes
  `<SelectItem key={item.id} value={item.id}>{`${item.name} (${agentTypeLabel(item.runtime)})`}</SelectItem>`;
- replace the agent `FieldDescription` (from `<FieldDescription className={agent && !agent.model_id …` to its closing
  `</FieldDescription>`) with:

```tsx
        <FieldDescription className={!agent && data.noModel ? 'text-destructive' : undefined}>{data.agents.length === 0
          ? 'Create one in Agents → New agent and choose its model.'
          : !agent && data.noModel ? `${data.noModel} has no model. Choose one on its General tab.`
          : `The agent works with its own model, prompt, instructions, skills and tools${agent?.model_name ? ` (${agent.model_name})` : ''}.`}</FieldDescription></Field>
```

- the What to document description becomes: `Code projects, and the code projects of the environments and services you
  choose, become the agent's read-only working folders (Claude Code) or code roots (Native). Environments, services
  and servers are described to the agent as text; this form gives no server access.`

`services/admin-ui/app/components/ClaudeCodeSessionsView.tsx`: import `sessionTypeLabel` from `~/lib/claudeCode`
and change the agent cell's second line to
`<p className="text-xs text-muted-foreground">{`${row.model_name} · ${sessionTypeLabel(row.runtime)}`}</p>`.

`services/admin-ui/app/components/ClaudeCodeSessionView.tsx`: replace the waiting alert with:

```tsx
      {session.status === 'starting' && !session.worker_active && <Alert><AlertDescription>{`Waiting for the ${session.runtime === 'openai' ? 'agent session' : 'Claude Code'} worker. The session starts when the worker is running.`}</AlertDescription></Alert>}
```

`services/admin-ui/app/components/RunsView.tsx`: `KINDS` becomes
`{ test: 'Agent test', board: 'Board task', claude_code: 'Claude Code', native_session: 'Native session' }`.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd services/admin-ui && pnpm exec tsc --noEmit && pnpm test`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-ui/app
git commit -m "feat(admin-ui): Generate documents offers native and Claude Code agents and shows each session's type

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Docs and full verification

**Files:**
- Modify: `docs/user-guide.md`, `docs/documentation.md`, `docs/architecture.md`, `docs/agent-profiles.md`,
  `deploy/README.md`, `services/admin-api/README.md`

- [ ] **Step 1: Update the docs**

`docs/user-guide.md`:
- **Architecture at a glance:** the workers line becomes
  `claude-code-worker (Claude Code sessions) · agent-session-worker (native sessions)`; after the
  `claude-code-worker` paragraph add: "`agent-session-worker` runs native agents' Generate documents sessions with the
  native runtime. Like `admin-api` it reaches RAG, PostgreSQL, MCP servers, model providers and SSH hosts, and runs
  `shell_run` in `shell-worker`; it reads `DATA_ROOT` read-only."
- **§4.13 Generate documents:** "lists Claude Code sessions" becomes "lists sessions of native and Claude Code
  agents"; **Prerequisites**: "An enabled agent of either type with a model that fits its type (Claude Code: a
  Claude connection; Native: a Responses or Claude connection)" and "Claude Code agents need at least one working
  folder … (native agents do not)"; **The form**: "**Agent**: an enabled agent with a model, shown with its type, for
  example `Technical writer (Claude Code)` or `API writer (Native)`; an agent without a model is not offered (from its
  Test tab the form says so)"; "the agent runs with its own model, prompt, instructions, skills and tools (Claude Code
  tools for Claude Code agents)"; after **What the agent receives** add **Native agents**: the chosen and linked code
  projects' folders and the agent's own code projects become its code roots (read-only, read with `list_files`,
  `read_file`, `search_files`, `shell_run`, `code_search` and `ast_grep`); its documents, SSH, MCP and services tools
  work as in its Test tab; each turn may take up to 5 minutes and 20 model calls, after which it fails and the next
  message continues from the last completed turn; **Stop** cancels the turn; a failed or stopped turn leaves the
  conversation and the draft as they were; tokens are recorded, cost is not.
- **Environment variables table:** add `| AGENT_SESSION_MAX_SESSIONS | Native sessions running at once per agent-session-worker (default 4) |`.
- **§7 Current limitations:** replace "Generate documents runs Claude Code agents only (native agents come with the
  session API) and describes servers as text (no SSH from the form)." with "Generate documents describes servers as
  text (no SSH from the form). The agent **Test** tab still uses test chats for native agents."
- **§4.16 Agent runs:** the run type filter offers `test`, `board`, `claude_code` and `native_session`.

`docs/documentation.md`, **Generate documents**:
- first paragraph: "starts an agent session (native or Claude Code); the agent writes one document. The agent runs with
  its own model, prompt, instructions, skills and tools; the form has no model or prompt field.";
- refusals: `NO_AGENT` ("Select an enabled agent.": unknown, disabled or the coordinator), `AGENT_MODEL` ("The agent has
  no enabled model for its agent type…": Claude Code needs a Claude connection, native a Responses or Claude
  connection), `NO_FOLDER` (Claude Code agents only);
- snapshots: `runtime` (`openai` | `claude_code`); `prompt_snapshot` holds `system_prompt` and `agent_version`, plus
  `claude_code_tools` for Claude Code; native sessions also store `agent_config` (the agent's config pinned at creation
  with the working folders as code roots via `native_sessions.code_roots`; credential references only) and `history`
  (Agents SDK items of successful turns, redacted); listing and detail return `runtime`, never `agent_config` or
  `history`;
- add **Native turns**: `agent-session-worker` claims native sessions (`session_worker.claim(…, 'openai')`), decrypts the
  pinned config's credentials per turn, runs `agents.agent(config, text, session=HistorySession, hooks=SessionEvents,
  timeout_seconds=300, max_turns=20, stream=False)`, writes the same events as Claude Code turns under
  `cc:<session>:<turn>` (`start.kind = native_session`), and commits the turn's history in the transaction that records
  the turn; failed, stopped and timed-out turns drop it. `SessionSettings(limit)` is not used (every turn sees the whole
  conversation, including the first message).

`docs/architecture.md`, after **Claude Code worker**, add a section **Agent session worker**: native Generate documents
sessions run in `agent-session-worker` (`knowledge_admin.knowledge_agents.native_sessions`), claimed with the same lease
code as `claude-code-worker` (`knowledge_agents/session_worker.py`: claims per agent type, queued messages delivered
once per turn, Stop, idle release after 5 minutes, interrupted turns failed on reclaim). Networks `default` and
`agent-shell` (no access to the `claude-code` network); `DATA_ROOT` read-only; `agent-workspaces` for tool scratch
space shared with `shell-worker`; Agents SDK traces go to Phoenix as for native runs in `admin-api`. History uses an
Agents SDK `Session` (`knowledge_agents/history.py`) backed by `agent_sessions_cc.history`.

`docs/agent-profiles.md`, **Persistence and APIs**, after the 0073 paragraph: "Migration `0074_native_agent_sessions`
adds `agent_sessions_cc.runtime`, `agent_config` and `history`; its down migration refuses while native sessions exist.
Native agents' Generate documents sessions run in `agent-session-worker`."

`deploy/README.md`:
- after the `claude-code-worker` bullet add: "`agent-session-worker` runs native agents' **Generate documents**
  sessions with the native runtime. It is on `default` (RAG, PostgreSQL, MCP servers, model providers, SSH hosts) and
  `agent-shell` (`shell-worker`), sees `DATA_ROOT` read-only and shares `agent-workspaces` with `shell-worker`. At
  most `AGENT_SESSION_MAX_SESSIONS` (default 4) sessions run at once.";
- add a section **Upgrading to native agent sessions** after **Upgrading to Generate documents**: run the migration
  (0074 adds three columns; existing sessions are Claude Code sessions); start the new service with
  `docker compose --env-file .env -f deploy/compose.yaml up -d agent-session-worker`; rolling 0074 back requires deleting
  native sessions first.

`services/admin-api/README.md`, after the paragraph about the session API passing `history` (line ~197–202): "Generate
documents sessions of native agents pass `session=HistorySession` instead (`knowledge_agents/history.py`, an Agents
SDK `Session`) with `stream=False` and `hooks=SessionEvents`; `agent()` refuses a session together with `history` or
streaming."

- [ ] **Step 2: Run every suite**

Run:
```bash
cd services/admin-api && uv run --frozen pytest -q
cd ../admin-ui && pnpm exec tsc --noEmit && pnpm test
cd ../.. && python3 -m unittest discover -s tests && bash db/tests/run.sh
```
Expected: everything passes. For any failure, check out `main` in a separate worktree and run the same test. Accept it
only if it also fails there, and record it in the PR description. Never run make targets.

- [ ] **Step 3: Check for leftovers**

Run: `git grep -n "Select an enabled Claude Code agent\|enabled Claude model\|runs Claude Code agents only\|native agents come with\|runtime === 'claude_code' && agent.enabled" -- services docs deploy`
Expected: no matches.

Run: `git grep -n "cc:{" -- services/admin-api/src`
Expected: only `knowledge_agents/session_worker.py` (`run_key`) and `claude_code_api.py` (the stream and history
queries, unchanged).

Run: `git grep -n "def claim\|def serve\|def next_turn\|def finish_turn\|def stop_requested" -- services/admin-api/src`
Expected: `session_worker.py` (`claim`, `next_turn`, `stop_requested`, `finish_turn`, `serve`), `claude_code_worker.py`
(`claim`, `serve` wrappers) and `native_sessions.py` (`serve`) only.

- [ ] **Step 4: Commit**

```bash
git add docs deploy/README.md services/admin-api/README.md
git commit -m "docs: Generate documents runs native and Claude Code agents (knowledge agents step 3)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

## Out of scope (say so in the PR)

- **The agent Test tab and the legacy test chats** (`/api/agents/{id}/runs`, `ka.agent_sessions`) stay as they are;
  moving them onto sessions is a later change. A native agent's Test tab keeps its chat; Generate documents is where
  native and Claude Code agents now run the same request.
- **The multi-agent board** keeps its own runner.
- **The tool gateway (step 2)**: Claude Code agents still have no catalog tools; native agents use their tools in-process
  as today.
- **Phoenix:** no changes. Native session turns are traced exactly like other native runs (`agents.agent` with
  `agent_tracing.setup()` in the worker).
- **The `/api/agent-sessions*` and `ClaudeCodeSession*` renames** and the `session:<id>:<turn>` run key format: a
  separate rename change (see Decisions).

## Follow-ups

- Rename routes, module and components (`/api/agent-sessions*`, `knowledge_agents/sessions.py`, `AgentSession*`).
- History compaction for long native sessions (the SDK's compaction-aware sessions), once sessions outgrow the context.
- The agent delete dialog says "Claude Code sessions" for what are now sessions of both types; reword it with the rename.
- Per-session turn limits (time, model calls) if 5 minutes and 20 calls prove short for documentation.
