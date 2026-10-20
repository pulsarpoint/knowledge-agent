# Session Review: Memory Notes and Instructions — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** "Suggest improvements" proposes memory notes (new shared memory entries) and an instructions change, never a
system prompt change; "Review and apply" saves the ticked notes through the memory module and applies the
instructions as today.

**Architecture:** The reviewer's output schema and prompt change in `agents/reviews.py` / `sessions/review.py`. Apply
writes instructions with the existing revision/fingerprint check and saves notes with `memory.store.create` (embedded
like the memory API), author = the person + the reviewed session. The Apply sheet shows instructions and a notes list.

**Tech Stack:** Python 3.14 / FastAPI / psycopg 3 / openai-agents 0.19 / pytest; React Router 7 / TypeScript / vitest.

**Spec:** `docs/superpowers/specs/2026-10-20-review-memory-design.md`.

## Global Constraints

- **Simple code, no unnecessary abstractions.** Reuse `memory.store.create`, `memory.store.validate` and the memory API's
  embedding path; do not write memory rows by hand.
- Work only in `.claude/worktrees/review-memory` (branch `feature/review-memory`). Never `make`, never touch
  `.env`/`.local`, never `git stash` (WIP commit instead), never touch the running Docker stack, never kill processes you
  did not start. UI checks: `ln -s ../../../../../services/admin-ui/node_modules services/admin-ui/node_modules` once
  (never commit it, never `pnpm install`).
- Format: `uv run --frozen ruff format <files>`, `pnpm format`. Commits end with `Co-Authored-By: Claude Opus 5.5
  <noreply@anthropic.com>`; never `git add` `.superpowers` or `node_modules`.
- Development version, test data: no shims; older reviews stored with `system_prompt` changes need not apply.
- Exact texts: an existing memory name is refused with the memory module's `An entry named <name> already exists.`
- At most 10 memory notes per review; note names and metadata follow `memory.store.validate`; metadata gets
  `source: review`.
- Full suites: `cd services/admin-api && uv run --frozen pytest -q -p no:warnings` (known failures: code-graph tests in
  worktrees, test_deploy docs command, test_environments rollback); `cd services/admin-ui && pnpm exec vitest run &&
  pnpm exec tsc --noEmit && pnpm format:check` (rerun slow tests alone).

## Review Focus

1. A review never changes the system prompt (schema has no field; apply refuses the field).
2. One refused note name does not stop the other notes or the instructions from applying, and the response says which
   names were refused.
3. Saving only notes skips the agent-changed check; applying instructions keeps it.
4. Memory entries saved from a review carry the person and the session as author and `source: review` metadata, and
   emit the memory events like any saved memory.
5. The reviewer still never sees connection fields or secrets (unchanged redaction).

---

### Task 1: Reviewer output and apply (backend)

**Files:** `services/admin-api/src/knowledge_admin/agents/reviews.py` (`InstructionChanges` → instructions only;
`TuningSuggestion` gains `memory_notes: list[MemoryNote]` (≤ 10; `name`, `content`, `metadata` dict of strings,
validated by `memory.store.validate`); keep the Bedrock double-encoding decode for `changes` and add it for
`memory_notes`; `source()` returns the notes with `exists` + current content per name; `ApplyInput` →
`{target, revision, fingerprint, instructions: str | None, memory_notes: list[MemoryNote]}`; apply: instructions via
`write_changes` with the check only when instructions are given, notes via `memory.store.create` with
`Author(actor_id, session_id)` and embedding like `memory/api.py`; response `{agent_id, saved: [{id, name}],
refused: [{name, message}]}`; audit details gain `instructions_changed`, `memory_saved`, `memory_refused`),
`sessions/review.py` (`TUNING_PROMPT` rewritten per the spec; `context.changes` = `{instructions}`,
`context.memory_notes`); tests `tests/test_agent_reviews.py` (update) + new cases.

- [ ] Step 1: failing tests — schema (notes + instructions, no system_prompt, >10 notes rejected, invalid name rejected,
  double-encoded notes decoded); source() marks existing names; apply: instructions only / notes only without matching
  fingerprint / both / one existing name refused while the rest applies / clone target / author + metadata + memory
  events / audit details; a request with `system_prompt` → 422. Step 2: fail. Step 3: implement. Step 4: focused, memory
  tests, full backend suite. Step 5: commit `feat(reviews): a review proposes memory notes and instructions, never the
  system prompt`.

### Task 2: Apply sheet and docs (UI)

**Files:** `services/admin-ui/app/features/sessions/SuggestionApply.tsx`, `RunSuggestions.tsx`,
`features/agents/reviews.ts`; tests `components/__tests__/suggestionApply.test.tsx`, `agentSessions.test.tsx`; docs
`docs/agents.md`, `docs/agent-profiles.md` (Suggest improvements / Review and apply), `docs/user-guide.md` if it
describes them.

- Sheet: advice; **Instructions** (current vs proposed, editable, "Apply instructions" checkbox, default on when a
  change is proposed); **Memory notes** (checkbox each, default on; editable name and content; "Already exists — open in
  Memory" link when `exists`, unticked by default); no system prompt section. Apply target (agent / new clone) stays,
  shown only when instructions are applied. After applying: saved notes link to their memory entries, refused names
  listed with the message.
- [ ] Step 1: failing vitest tests — no system prompt section; request body for instructions only / notes only / both;
  existing note unticked with link; refused names shown. Step 2: fail. Step 3: implement. Step 4: vitest, tsc,
  format:check. Step 5: commit `feat(admin-ui): apply review memory notes and instructions`.
