# Session Review (Section 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A reviewer chat on a finished session that explores it with the session explorer, proposes where an
improvement belongs (instructions, skill, memory) and makes the change only after the person agrees in the chat.

**Architecture:** A `review` run purpose with a pinned config built in code (reviewer prompt, the reviewed session's
run key, the reviewed agent and its revision, the agent's model) running through the normal session worker; a fixed
reviewer tool set (explorer tools bound to the pinned session, `get_agent`, memory read/save, skill file reads,
`update_agent_instructions`, `create_skill`); the UI reuses the agent chat component inside a wide Sheet. The old
single-shot review is removed.

**Tech Stack:** Python 3.14 / FastAPI / psycopg 3 / OpenAI Agents SDK / pytest; React Router 7 / TypeScript / shadcn/ui /
vitest; PostgreSQL 17.

**Spec:** `docs/superpowers/specs/2026-10-28-session-review-design.md`

## Global Constraints

- Migration `0147_session_review`: `agent_sessions.purpose` CHECK gains `'review'`; drop `ka.agent_session_reviews`
  (test data); down restores both (empty table).
- Reviewer tools are exactly the spec's list; no catalog tools, resources, SSH or experts; explorer tools only ever load
  the pinned `review_of` session (never an argument).
- `create_skill` always creates the skill **disabled**; `update_agent_instructions` checks the revision, writes the
  audit entry (old/new text, review session) and never touches the system prompt.
- Exact texts: no model — `Select an enabled model in this agent’s General tab.`; working — `Wait for the agent to
  finish before reviewing it.`; not a saved agent's session — `Reviews are for a saved agent's chats and event runs.`;
  revision changed — `The agent changed since you read it. Read it again with get_agent and propose the change again.`;
  skill name exists — `A skill with this name already exists.`
- UI: "Improve agent" opens a right Sheet with the same chat component as the agent Chat tab (no second chat UI);
  conventions: toast on success, inline Alert on errors, Conversations sheet for earlier reviews.
- Work only in `.claude/worktrees/session-review` (branch `feat/session-review`). Never `make`, never touch `.env`/
  `.local`, never `git stash` in any form, never touch the running Docker stack, never kill processes you did not start,
  never spawn subagents. `node_modules` is a symlink: never commit it, never `pnpm install`.
- Format: `uv run --frozen ruff format <files>`, `pnpm format`. Commits end with `Co-Authored-By: Claude Opus 5.5
  <noreply@anthropic.com>`; never `git add` `.superpowers` or `node_modules`.
- Suites: backend `cd services/admin-api && uv run --frozen pytest -q -p no:warnings` (to completion; known failures:
  code-graph tests in worktrees, test_deploy docs command, test_environments rollback); UI `cd services/admin-ui &&
  pnpm exec vitest run && pnpm exec tsc --noEmit && pnpm format:check` (rerun slow files alone); root
  `python3 -m unittest discover -s tests`.

## Review Focus

1. A reviewer can never read another session: the explorer tools are bound to the pinned run key even if the model
   passes ids in arguments (Task 1).
2. `update_agent_instructions` refuses on a stale revision and leaves the system prompt and every other agent field
   unchanged (Task 1).
3. A created skill is disabled and not visible to any run until a person enables it (Task 1).
4. The review chat shows tool calls and results live exactly like the agent chat, and a review of a session can be
   reopened from Conversations (Task 2).
5. Starting a review on a working session, a session without a saved agent, or an agent without a model is refused
   with the exact texts (Tasks 1–2).

---

### Task 1: Review runs and reviewer tools (backend)

**Files:** migration `db/migrations/0147_session_review.{up,down}.sql` + migration test + `docs/migrations.md`;
`sessions/start.py` (start a review run: `start_review(conn, project, run_key, actor)` → session with purpose
`review`, pinned config `{review_of, reviewed_agent_id, reviewed_revision, instructions, model}`); a new
`sessions/review_agent.py` (reviewer prompt constant; `reviewer_tools(context)`; `get_agent`,
`update_agent_instructions`, `create_skill`); `agents/runner.py` / `knowledge_agents/native_sessions.py` (a `review`
run gets exactly the reviewer tools, explorer tools from `sessions/explorer.py` bound to the pinned session); skills
store (create a disabled skill folder with `SKILL.md`, source "created by review"); API `POST
/api/agent-runs/{run_key}/reviews` (start; returns the session) and `GET /api/agent-runs/{run_key}/reviews` (earlier
review sessions); remove `sessions/review.py`'s single-shot review, `agents/reviews.py` apply flow, their routes and
tests; `docs/agents.md`.

- [ ] Failing tests (Review Focus 1, 2, 3, 5; tool set exact; audit entry; start rules; old routes gone) → implement →
  full backend suite → commit `feat(sessions): review runs: a reviewer agent with explorer tools proposes and applies agent improvements`.

### Task 2: Improve agent chat (UI)

**Files:** an "Improve agent" button on the run record page and the saved agent's chat (finished sessions only,
disabled with the reason otherwise); a wide right Sheet reusing the agent chat component (`AgentChat` / `ChatColumn`)
for the review session, with the stream, tool calls, Stop, and a Conversations button listing earlier reviews of that
session (`GET …/reviews`); first message box prompts for feedback ("What should the agent do differently?");
remove `RunSuggestions.tsx`, `SuggestionApply.tsx`, `features/agents/reviews.ts` and their tests; `docs/user-guide.md`.

- [ ] Failing tests (Review Focus 4, 5; Sheet opens with the shared chat; earlier reviews listed; old UI gone) →
  implement → UI suite → commit `feat(ui): Improve agent opens a reviewer chat in a side sheet`.
