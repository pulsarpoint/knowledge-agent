# Tool Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Catalog tools can be switched off for a saved agent and, further, for a run; off tools are never offered to
the model.

**Architecture:** One column (`ka.agents.disabled_tools`), one request field (`disabled_tools`), one place that
withholds tools (`agents/runner.instructions_and_tools`), and one Tools list component used by the agent settings and
the chat's Agent settings.

**Tech Stack:** PostgreSQL 17; Python 3.14 / FastAPI / psycopg 3 / pytest; React Router 7 / TypeScript / shadcn/ui /
vitest.

**Spec:** `docs/superpowers/specs/2026-10-24-tool-selection-design.md`

## Global Constraints

- **Simple code;** the narrowing rule is a pure function: `disabled = agent ∪ request` for a saved agent, `request` for
  an ad-hoc run; subagents and experts inherit the caller's set (never fewer off).
- Exact texts: "Unknown tool: <name>." (400); "Off for this agent"; "N of 37 tools on" (N and the total computed from the
  catalog, not hard-coded).
- Off tools are stored (`disabled_tools`), so new catalog tools are on by default. Scope: catalog tools only (MCP and
  experts unchanged).
- Migration `0135_agent_disabled_tools` (up/down).
- Work only in `.claude/worktrees/tool-selection` (branch `feature/tool-selection`). Never `make`, never touch
  `.env`/`.local`, never `git stash` in any form, never touch the running Docker stack, never kill processes you did not
  start, never spawn subagents. UI: `node_modules` is a symlink (never commit it, never `pnpm install`).
- Format: `uv run --frozen ruff format <files>`, `pnpm format`. Commits end with `Co-Authored-By: Claude Opus 5.5
  <noreply@anthropic.com>`; never `git add` `.superpowers` or `node_modules`.
- Full suites: `cd services/admin-api && uv run --frozen pytest -q -p no:warnings` (known failures: code-graph tests in
  worktrees incl. test_code_processing, test_deploy docs command, test_environments rollback);
  `cd services/admin-ui && pnpm exec vitest run && pnpm exec tsc --noEmit && pnpm format:check` (documentSaves and
  skills are slow: rerun alone).

## Review Focus

1. A saved agent with `ssh_agent` off, run by an event handler → `ssh_agent` not offered, and the backstop refuses a
   forged call (Task 1).
2. A chat request that tries to switch an agent's off tool back on (omits it from `disabled_tools`) still has it off
   (Task 1).
3. An expert called from a run with `search_documents` off does not get `search_documents` (Task 1).
4. "Start new chat with these settings" keeps the chat's off tools (Task 2).
5. A tool added to the catalog after an agent saved its list is on for that agent (Task 1).

---

### Task 1: Backend — storage, runs, subagents, API

**Files:** `db/migrations/0135_agent_disabled_tools.{up,down}.sql` + migration test; `access.py` or a small
`tools/selection.py` (pure `disabled_for(agent_disabled, requested) -> frozenset` and `validate(names)`);
`sessions/start.py` (request field, pinned `disabled_tools`, saved-agent union, ad-hoc), `sessions/rows.py` / session
detail (`disabled_tools`), `agents/runner.py` (`instructions_and_tools` withholds `config['disabled_tools']`),
`knowledge_agents/experts.py` and `subagents.py` (inherit), `knowledge_agents/config.py` (load the agent's column),
`agents/profiles_api.py` (read/write, validated), `sessions/debug.py` (unchanged behaviour, now reflecting it), tests
(`test_tool_selection.py` for the pure rules and Review Focus 1–3, 5; profiles, start, debug/preview, expert tests),
`docs/agents.md`.

- [ ] Failing tests → implement → full backend suite → commit
  `feat(tools): agents and runs can switch catalog tools off`.

### Task 2: UI — the Tools list in agent settings and chat settings

**Files:** create `features/agents/ToolsList.tsx` (grouped checkboxes from the tool catalog API — find the existing
endpoint listing tools with group and description, e.g. the one the Debug panel or agent tools page uses; All / None per
group; count; `locked` names shown off and disabled with "Off for this agent"); `features/agents/AgentProfileSettings.tsx`
(Advanced → Tools, saves `disabled_tools`), `features/agents/api.ts`; `features/sessions/AgentSettingsSheet.tsx`
(Advanced → Tools; agent's off tools locked), `features/sessions/agentSettings.ts` (carries `disabled_tools` into the
start request and "Start new chat with these settings"), `features/sessions/api.ts` types; tests (`ToolsList.test.tsx`,
agent settings, settings sheet, start-new-with-settings — Review Focus 4); `docs/user-guide.md`.

- [ ] Failing tests → implement → UI suite → commit `feat(admin-ui): Tools under Advanced in agent and chat settings`.
