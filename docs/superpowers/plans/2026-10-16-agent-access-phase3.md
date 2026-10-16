# Agent Access Phase 3 (Tools Always On, Skills On Demand) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every run gets every tool (catalog tools, the tools of the MCP servers in its access, `run_subagent`, every
expert); the access object alone decides what a tool may do; per-agent tool choice, "needs", usage overrides and request
tool switches are gone; skills are listed in the instructions at creation and loaded with `load_skill(name)`.

**Architecture:** `effective_tools` becomes "the whole catalog"; each tool's `check_<tool>` (Phase 1) refuses what the
access object does not allow. Ad-hoc writing runs ask for the content editor role explicitly (`content_editor` in the
start request) instead of through tool switches. Skills follow the Agents SDK convention (list at creation, full text on
use) with a database-backed `load_skill`.

**Tech Stack:** PostgreSQL 17, Python 3.14 / FastAPI / psycopg 3 / openai-agents 0.19 / pytest; React Router 7 /
TypeScript / vitest.

**Spec:** `docs/superpowers/specs/2026-10-16-agent-access-design.md` (Phase 3 of 3; then the Phase 4 cleanup). Phases
1–2 merged (015cdb8, e393965).

## Global Constraints

- **Simple code, no unnecessary abstractions; rules testable in isolation** (access decisions only in `access.py` and
  the per-tool `check_<tool>` functions; the access matrix test covers every catalog tool).
- Work only in `.claude/worktrees/agent-access-3` (branch `feature/agent-access-3`). Never `make`, never touch
  `.env`/`.local`, never `git stash` (WIP commit instead), never touch the running Docker stack, never kill processes you
  did not start. UI checks: `ln -s ../../../../../services/admin-ui/node_modules services/admin-ui/node_modules` once
  (never commit it, never `pnpm install`).
- Format: `uv run --frozen ruff format <files>`, `pnpm format`. Commits end with `Co-Authored-By: Claude Opus 5.5
  <noreply@anthropic.com>`; never `git add` `.superpowers` or `node_modules`.
- Development version, test data: no shims, no redirects; migrations correct up and down. Next migration: **0115**.
- User decisions (2026-10-16): grounded ("Answer only from documents") runs get the same tools as every run — the
  grounding policy in the prompt and the existing answer validation stay; `run_subagent` and every expert are offered to
  every saved-agent run (a subagent gets every tool except `run_subagent`, with narrowed access; a run may still narrow
  experts with `expert_ids`); the per-agent Subagents switch and expert choice go.
- Exact texts: `report_not_covered` outside a grounded run refuses `This run does not answer only from documents.`;
  `load_skill` refuses `This skill is not available in this run.` (Phase 1 `refuse_use('skill')`).
- Skills section (in the agent's instructions, only when the run has skills), adapted from the SDK's
  `agents/sandbox/capabilities/skills.py` for skills stored in the database (no files):
  ```
  ## Skills
  A skill is a set of instructions to follow. Below is the list of skills that can be used. Each entry has a name and a description; call `load_skill` with the name to get its full instructions when you use it.
  ### Available skills
  - <name>: <description>
  ### How to use skills
  - Trigger rules: If the user names a skill OR the task clearly matches a skill's description shown above, you must use that skill for that turn. Multiple mentions mean use them all. Do not carry skills across turns unless re-mentioned.
  - Call `load_skill` once per skill, before following it.
  - Missing/blocked: If a named skill isn't in the list, say so briefly and continue with the best fallback.
  ```
- Full suites: `cd services/admin-api && uv run --frozen pytest -q -p no:warnings` (known failures: code-graph tests in
  worktrees, test_deploy docs command, test_environments rollback); `cd services/admin-ui && pnpm exec vitest run &&
  pnpm exec tsc --noEmit && pnpm format:check` (rerun slow tests alone).

## Review Focus

1. Every run's tool list is the whole catalog (+ its MCP servers' tools, + experts, + `run_subagent` except in
   subagents), whatever the agent, the request or the grounded switch.
2. A plain resource chat still cannot propose, save or edit (content editor off); the Propose wizard, Documents → New
   and Ask agent runs still can.
3. `load_skill` returns a listed skill's full text and refuses any other; skill text never appears in the prompt.
4. Nothing reads `ka.agent_tools`, needs, usage overrides, `proposal_tools` or request tool lists after Task 2 (grep).
5. The access matrix test covers `load_skill` and still covers every catalog tool.

---

### Task 1: Every run gets every tool

**Files:** `tools/catalog.py` (`effective_tools`, `needs_met`, `RUN_ONLY_NEEDS`, `NEED_*`, `has_resources`,
`capability_tools`, `enabled_capability_tools`, `mcp_blocked`/`mcp_allowed`/`mcp_notes`, `ALWAYS_ON`, `PROPOSAL_TOOLS`,
`run_only`), `tools/tools.json` (`needs` removed), `tools/fixture.py`, `agents/runner.py` (`instructions_and_tools`, MCP
filters, "Tool notes"), `sessions/start.py` (`StartInput.tools`, `choosable`, `usable_tools`, `REQUIRES`, `NOT_ITS_TOOL`,
`proposal_tools`, writing-run rule: ad-hoc `content_editor` request field), `knowledge_agents/ephemeral.py`,
`knowledge_agents/subagents.py`, `knowledge_agents/experts.py`, `capabilities/documents.py`
(`check_report_not_covered`), `sessions/debug.py`; tests.

- `effective_tools(config)` returns every catalog tool with its built-in description (no modes, no needs, no usage
  overrides); subagent configs drop `run_subagent`. MCP: every tool of every MCP server in the run's access (no
  filters, no "Tool notes"). Experts: every enabled expert agent other than the agent itself, narrowed by the request's
  `expert_ids` when given.
- `check_report_not_covered` refuses outside a grounded run with the exact text.
- Start request: `tools` and `subagents` fields go (unknown fields are a 400 as today). Ad-hoc runs take
  `content_editor: bool` (default false) — the Propose wizard sends it; Documents → New and Ask agent keep Phase 1's
  rule. Saved agents: a request may only switch the role off (`content_editor: false`), never on.
- `tools.json` loses `needs`; `ka.tools.needs` is left to Task 2's migration (the fixture stops writing it).
- [ ] Step 1: failing tests — a saved agent, an ad-hoc run, a grounded agent and a subagent each get the expected full
  tool list; MCP tools of a server in access are all offered; experts all offered and narrowed by `expert_ids`;
  `report_not_covered` refusal; a request with `tools` is a 400; the Propose wizard request with `content_editor: true`
  can propose, a plain resource chat cannot; a saved agent request cannot switch the role on. Step 2: fail. Step 3:
  implement. Step 4: focused, matrix, full backend suite. Step 5: commit `feat(tools): every run gets every tool`.

### Task 2: Drop per-agent tool storage

**Files:** `db/migrations/0115_tools_always_on.up.sql`/`.down.sql` (drop `ka.agent_tools`, `ka.tools.needs`; down
recreates them empty with the original columns and constraints); `agents/tools_api.py` (remove `GET/PUT
/agents/{id}/tools`, `flags`, `switch_on_defaults`, `switch_subagents`, `subagents_on`, `DISCOVERY_ONLY`; keep
`GET /tools` only if Task 4's UI still needs a read-only catalog — otherwise remove), `agents/profiles_api.py`
(`subagents`, `tools_summary`, expert row deletes), `agents/wizard.py` (tool choice), `agents/clones.py` (tool rows),
`knowledge_agents/config.py` (`tool_items`, `native_tools`, `sections()` Tools, `SECTIONS`), `tools/README.md`;
tests and migration tests that touch `agent_tools`.

- [ ] Step 1: failing tests — the dropped table/column are gone and come back on down; removed endpoints 404; wizard and
  PUT refuse `tools`/`subagents`; clone round trip without tools. Step 2: fail. Step 3: implement; grep every dropped
  name (Review Focus 4). Step 4: full backend suite. Step 5: commit `refactor(tools): drop per-agent tool storage`.

### Task 3: Skills on demand

**Files:** new `capabilities/skills.py` (`load_skill(name)` tool + `check_load_skill`), `tools/tools.json` (entry),
`tools/catalog.py` (import for registration), `agents/runner.py` (`get_prompt`: the skills section from Global
Constraints, names and descriptions only, from the run's skill access entries; the old `# Skills` JSON block goes),
`sessions/text.py` (`agent_system_prompt`: same section, no full text), `sessions/debug.py`; tests (matrix rows, tool
schema snapshot, prompt snapshots).

- `load_skill(name)`: the check allows a skill entry in the run's access with that name (`access.entry` by type
  `skill`, matched on `name`); the work reads `instructions` from `ka.agent_skills` by id (enabled skills only; a
  disabled one refuses like a missing one).
- [ ] Step 1: failing tests — the prompt lists names + descriptions in the exact section text and contains no skill
  instructions; `load_skill` returns the text of a listed skill, refuses an unlisted or disabled one with the exact
  text; matrix rows for `load_skill`; a run without skills has no section. Step 2: fail. Step 3: implement. Step 4:
  focused, matrix, full backend suite. Step 5: commit `feat(skills): skills listed at creation, loaded with load_skill`.

### Task 4: UI and docs

**Files:** `services/admin-ui/app/features/agents/` (`AgentDetailView.tsx` tabs → `general, resources, test`;
`AgentToolsView.tsx` and `tools.ts` removed; `AgentProfileSettings.tsx` Subagents switch removed; `AgentWizard.tsx` and
`wizard.ts` tool choice and `accessSwitches` removed; `AgentsView.tsx` and `api.ts` `tools_summary` removed),
`features/sessions/` (`agentSettings.ts`, `AgentSettingsSheet.tsx`: tool switches and the subagents switch removed,
experts picker kept; `api.ts` `StartInput`), `ProposeWizard.tsx` (sends `content_editor: true` instead of tools),
`ResourceChats.tsx`, `DocumentEditorView.tsx`, `AgentTestView.tsx`, `AgentChat.tsx` callers; docs
(`docs/agent-profiles.md`, `docs/user-guide.md`, `docs/skills.md`); tests.

- [ ] Step 1: failing vitest tests — tabs; settings sheet without tool switches; Propose wizard start body; wizard
  without tools. Step 2: fail. Step 3: implement. Step 4: vitest, tsc, format:check. Step 5: commit
  `feat(admin-ui): tools always on — Tools tab and tool switches removed`.
