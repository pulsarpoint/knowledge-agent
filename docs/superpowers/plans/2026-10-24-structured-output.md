# Structured Output Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A run can be started with `output_schema_id` so the agent finishes with JSON matching a versioned schema from a
shared library; the output is validated, stored, emitted as `run.output`, and readable over REST (with run-and-wait and
a status endpoint).

**Architecture:** A versioned schema library (tables, pure subset check, REST). Run start pins a schema version; the
runner sets an `AgentOutputSchemaBase` over the JSON Schema as the agent's output type (with an instructions fallback for
models without structured output), validates with `jsonschema`, retries once, stores the output and emits the event.
Reading endpoints and the UI sit on top.

**Tech Stack:** PostgreSQL 17; Python 3.14 / FastAPI / psycopg 3 / pydantic / `jsonschema` / OpenAI Agents SDK 0.19 /
pytest; React Router 7 / TypeScript / shadcn/ui / vitest.

**Spec:** `docs/superpowers/specs/2026-10-24-structured-output-design.md`

## Global Constraints

- **Simple code;** `check_schema` and the validation/retry decision are pure functions tested in isolation.
- Exact texts: "Chats do not take an output schema."; "Used by: <handler names>."; "Output did not match the schema";
  "Output is larger than 256 KB."; the retry message "Your final answer does not match the output schema: <errors>.
  Answer again with JSON only."; the fallback block "Finish with JSON only, matching this JSON Schema: …".
- Limits: schema 64 KB, output 256 KB, `wait` 1–120 seconds.
- Migration `0136_output_schemas` (up/down). `jsonschema` is added to admin-api with `uv add` (lock updated) only if
  not already a dependency — check first.
- Chats never take a schema; agents carry no schema; the prompt changes only in the model fallback.
- Work only in `.claude/worktrees/structured-output` (branch `feature/structured-output`). Never `make`, never touch
  `.env`/`.local`, never `git stash` in any form, never touch the running Docker stack, never kill processes you did
  not start, never spawn subagents. UI: `node_modules` is a symlink (never commit it, never `pnpm install`).
- Format: `uv run --frozen ruff format <files>`, `pnpm format`. Commits end with `Co-Authored-By: Claude Opus 5.5
  <noreply@anthropic.com>`; never `git add` `.superpowers` or `node_modules`.
- Full suites: `cd services/admin-api && uv run --frozen pytest -q -p no:warnings` (known failures: code-graph tests in
  worktrees incl. test_code_processing, test_deploy docs command, test_environments rollback);
  `cd services/admin-ui && pnpm exec vitest run && pnpm exec tsc --noEmit && pnpm format:check` (documentSaves and
  skills are slow: rerun alone).

## Review Focus

1. The same saved agent started twice, once with a schema and once without → the first finishes with validated JSON
   and a `run.output` event, the second answers normally (Task 2).
2. A schema edited while a run is in progress → the run validates against the version it pinned (Task 2).
3. A model answering invalid JSON twice → the run fails with the errors and raw answer kept, no `run.output` event
   (Task 2).
4. `POST /api/sessions?wait=30` on a run that takes longer → 202 with the id; `/status` then reports progress and
   finally `output_ready` (Task 3).
5. Deleting a schema a handler uses → refused with the handler names (Task 1).

---

### Task 1: The schema library (backend)

**Files:** `db/migrations/0136_output_schemas.{up,down}.sql` (output_schemas, output_schema_versions,
`event_handlers.output_schema_id` + `output_schema_version` nullable, `agent_sessions.output jsonb`,
`output_schema_id`, `output_schema_version`) + migration test; a new package `output_schemas/` (`rules.py` with pure
`check_schema(schema) -> list[str]` and `validate(schema, value) -> list[str]`; `store.py`; `api.py` with CRUD,
versions, `POST /api/output-schemas/check`, delete refused while used); router wiring; tests
(`test_output_schema_rules.py`, `test_output_schemas_api.py`); `docs/agents.md` (output schemas).

- [ ] Failing tests (subset accepted/refused with paths, sample validation, versions on changed save only, delete
  refused — Review Focus 5) → implement → full backend suite → commit `feat(output): a versioned output schema library`.

### Task 2: Runs with a schema

**Files:** `sessions/start.py` (`output_schema_id`, `output_schema_version`; chat purposes refused; pin `{id, name,
version, schema}`; runtime and saved agents), `events/processor.py` / handler start path (handler's schema and version),
`events/handlers_api.py` (fields), `agents/runner.py` (`JsonSchemaOutput(AgentOutputSchemaBase)`; the model capability
`supports_structured_output` from `llm/` settings and the instructions fallback; validation, one retry, limits, failure
reason), the session worker that finishes a run (store `output`, emit `run.output` with the spec's payload),
`llm/` capability field + LLM settings default for OpenAI-style models, `sessions/debug.py` (pinned schema), tests
(`test_structured_output_runs.py`: Review Focus 1–3, fallback, limits, handler start, chat refusal).

- [ ] Failing tests → implement → full backend suite → commit `feat(output): runs finish with validated JSON when started with a schema`.

### Task 3: Reading the output

**Files:** `sessions/api.py` (`GET /api/sessions/{id}/output`, `GET /api/sessions/{id}/status`, `POST /api/sessions`
`wait` query param — waits on the session's completion without blocking the event loop: poll the session row with
`asyncio.sleep` steps or reuse the existing session events notification if there is one), runs list filter
`has_output`, tests (`test_session_output_api.py`: Review Focus 4, finished/failed/no-schema/running states,
wait bounds), `docs/agents.md` API section.

- [ ] Failing tests → implement → full backend suite → commit `feat(output): output, status and run-and-wait endpoints`.

### Task 4: The UI

**Files:** `features/outputSchemas/` (list page, editor with JSON editor + check result + sample **Validate sample**,
version list, delete with AlertDialog), route + sidebar entry (agents area), the agent **Test** tab and **Run agent**
(Output schema picker: none / a schema, optional version), the handler editor (Output schema + version or "current"),
the run page **Output** panel (formatted JSON + copy; errors + raw for a failed run), runs list "Has output" filter, LLM
model form **Supports structured output** switch, tests for each, `docs/user-guide.md`.

- [ ] Failing tests → implement → UI suite (+ backend if touched) → commit `feat(admin-ui): output schemas, pickers and the run Output panel`.
