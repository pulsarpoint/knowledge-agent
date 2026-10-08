# Remove Claude Code agents — design

Date: 2026-10-08. Status: approved in conversation.

## Goal

The app has one kind of agent: native. The Claude Code runtime, its worker container, its SDK and every UI option for it
are removed, and the names that still say "claude code" on code native agents use are renamed.

User decisions:
- Remove the runtime **and** rename everything that keeps the old name, including the `ka.agent_sessions_cc` table.
- Existing data in other installs: Claude Code agents are converted to native agents; Claude Code sessions are deleted
  (saved documents are kept). The live database has neither (4 native agents, 3 closed native document sessions).
- `/api/claude-code/...` is removed outright, not aliased: the only client is our UI, changed in the same commit.
- `scripts/benchmarks/claude_api.py` (standalone benchmark with its own Dockerfile) stays.

Out of scope: old superpowers specs and plans (history), the benchmark script.

## Removed

### Deploy
- `deploy/compose.yaml`: the `claude-code-worker` service, the `claude-code` network (and its use by postgres), the
  `claude-code-home` volume.
- `deploy/Dockerfile.admin-api`: the Claude Code CLI setup and the `MAX_SLOTS` check.
- `services/admin-api/pyproject.toml`: `claude-agent-sdk`, the `knowledge-claude-code-worker` and `knowledge-claude-cli`
  scripts (lock file updated).
- `deploy/README.md`, `services/admin-api/README.md`: their Claude Code sections.

### Backend
- `claude_code_worker.py`, `claude_code_cli.py` and their tests.
- From `claude_code.py` everything Claude Code-only (`ALLOWED_TOOLS`, `DISALLOWED_TOOLS`, `SYSTEM_PROMPT` if only
  Claude Code agents use it); what native code uses moves (see Renames).
- `knowledge_agents/config.py`: `CHOOSE_CLAUDE`, `ClaudeCodeTool`, `normalize_tools`, the `claude_code` branch of
  `model_fits` (a model fits when it is an enabled Responses or Claude connection; the runtime argument goes), and
  `runtime` / `claude_code_tools` in the AgentConfig shape.
- `profiles_api.py`: `runtime` and `claude_code_tools` in `ProfileInput` and responses; the runtime-switch code (Claude
  model check, `runtime` key in the `agent.updated` audit, `ensure_native_tools` on switch, subagents removal on
  switch). Sending `runtime` or `claude_code_tools` is a 400 (extra fields are forbidden).
- `agent_wizard.py`, `agent_clones.py`, `tools_api.py`, `tools/catalog.py`, `llm_api.py`, `agent_sessions.py`,
  `generate_documents.py`, `knowledge_agents/*`: every `runtime` / `claude_code` branch. Native behaviour is unchanged.
- Session worker and native sessions: `claude_session_id` and `cost_usd` are no longer written; usage keeps input,
  output, cached and reasoning tokens and model calls.

### UI
- Agent type select in the wizard (step 1 becomes **Agent model** + **Resources**) and on General.
- Claude Code badge, Claude Code tools card, `claudeCodeNotes` on the tool tabs, the Claude Code Test card (Test is
  always the native test chat).
- The model filter by runtime: one rule, `codeCompatible`.
- `runtime` / `claude_code_tools` in `AgentProfile`, `AgentProposal`, `saveAgent`, `createWizardAgent`, session types.

### Docs
`docs/user-guide.md`, `docs/agents.md`, `docs/agent-profiles.md`, `docs/architecture.md`, `docs/documentation.md`,
`docs/prompts/api-documentation-workflow.md`: Claude Code is described nowhere as a current feature.

## Renames

| Now | After |
| --- | --- |
| `ka.agent_sessions_cc`, constraints and indexes `agent_sessions_cc_*` | `ka.agent_sessions`, `agent_sessions_*` |
| `/api/claude-code/context`, `/api/claude-code/sessions/...` | `/api/generations/context`, `/api/generations/sessions/...` |
| `claude_code_api.py` | `generations_api.py` |
| `claude_code.py` (`FINAL_ANSWER`, `close_idle`, `agent_system_prompt`) | `session_text.py` |
| `lib/claudeCode.ts` | `lib/generations.ts` |
| `ClaudeCodeSessionView`, `ClaudeCodeSessionsView` | `GenerationView`, `GenerationsView` |
| audit `claude_code.session.*` | `generation.session.*` (existing audit rows keep their names) |

Test files follow their modules (`test_claude_code_sessions.py` → `test_generations.py`, etc.). UI routes
(`/documentation/generations/...`) are unchanged.

## Migration `0083_remove_claude_code`

Up, in one transaction:
1. `DELETE FROM ka.agent_sessions_cc WHERE runtime='claude_code'` (inputs and reviews cascade; sessions point to
   documents, not the other way, so saved documents are untouched).
2. Claude Code agents without tool rows get the tools switching to native gives them today (`ensure_native_tools`),
   seeded in SQL the way `0078_agent_tools` did: a `VALUES` list of (tool, offered-when) per capability, with the per-run
   rule (`enabled_capability_tools`: every tool of each enabled capability, never `run_subagent`) and the fixed rule
   (`implied_tools`, the 0078 backfill) copied from `tools/catalog.py` as of this change. A test compares the seeded
   rows with `ensure_native_tools` for the same agent.
3. Drop `ka.agents.runtime`, `ka.agents.claude_code_tools` and their checks (`claude_code_tools_known`, ...).
4. Drop `runtime`, `claude_session_id`, `cost_usd` from the sessions table and the checks that use them; rewrite the
   checks that mention runtime (ad-hoc: `agent_id IS NOT NULL OR (purpose='documents' AND agent_config IS NOT NULL)`;
   test sessions: native only becomes no check).
5. Rename the table to `ka.agent_sessions` and every `agent_sessions_cc_*` constraint and index to `agent_sessions_*`.

Down: rename back; re-add `runtime text NOT NULL DEFAULT 'openai'`, `claude_code_tools` (all four tools),
`claude_session_id text NULL`, `cost_usd numeric NOT NULL DEFAULT 0` and the old checks. Deleted Claude Code sessions
are not restored.

The db tests that pin the rollback target move from 81 to 82.

## Errors

| Problem | Behaviour |
| --- | --- |
| Client sends `runtime` or `claude_code_tools` | 400 validation |
| Client calls `/api/claude-code/...` | 404 |
| An install still has Claude Code agents / sessions | Converted / deleted by the migration |

## Testing

- Delete: Claude Code worker, CLI, agent and session tests; UI Claude Code cases.
- Rewrite: tests that set `runtime` only to build native agents.
- Migration: a Claude Code agent with a session is converted (native tools on, no runtime column) and its session
  deleted; a native session survives the rename with its inputs; down then up.
- API: `/api/generations/...` works end to end (create, list, detail, events, save document); `/api/claude-code/...`
  is 404; `runtime` in a profile save is 400.
- UI: wizard step 1 has Agent model and Resources only; General has no Agent type; Test tab always shows the native
  chat; generation pages call `/api/generations/...`.
- Full suites green except the known pre-existing failures.

## Shipping

Own worktree and branch, subagent-driven execution, merged like the previous change. After merge, rebuild with
`docker compose up -d --build --remove-orphans` so the old worker container is removed.
