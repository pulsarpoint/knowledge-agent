# Tool Selection — Design

Approved direction: 2026-10-24 (conversation). Partly reverses migration 0115 ("every run gets every tool"): access
still decides what a tool may touch, but a person can now switch catalog tools off for an agent and for a run.

## Problem

- Every run is offered all 37 catalog tools. Asked about a service, an agent reaches for `ssh_agent` instead of reading
  the documents.
- Testing how an agent finds and answers from documentation needs runs limited to the document tools; today there is no
  way to do that.

## Decisions (from the conversation)

1. **Tools are selectable in two places:** a saved agent's settings and a chat's Agent settings, both under
   **Advanced**.
2. **A run can only switch more off.** A saved agent's off tools stay off in every run of it (chats, events, schedules,
   Jira); a chat's settings switch further tools off, never one back on.
3. **Off means not offered.** The model never sees an off tool, so it cannot call it.

## 1. Storage and runs

- Migration `0135_agent_disabled_tools`: `ka.agents.disabled_tools text[] NOT NULL DEFAULT '{}'`. Off tools are stored,
  so a tool added to the catalog later is on for every agent. Down: drop the column.
- Run start (`sessions/start.py`): the request takes `disabled_tools: list[str]` (catalog names; unknown names → 400
  "Unknown tool: <name>."). A saved agent's run disables the union of the agent's and the request's; an ad-hoc run
  disables the request's. The pinned configuration records `disabled_tools`.
- `agents/runner.instructions_and_tools` adds `config['disabled_tools']` to `withheld`, so the offered tools, the
  adapter's backstop (`context['tools']`), the Debug tab and the start preview all follow it.
- Subagents and experts inherit the calling run's `disabled_tools` (they may withhold more, as today, never less).
- Scope: the catalog tools. MCP servers and experts already have their own per-run selection and stay as they are.
- The session detail returns `disabled_tools`; **Start new chat with these settings** keeps them.

## 2. Saved agents

- The agent profile API (`agents/profiles_api.py`) reads and writes `disabled_tools` (validated against the catalog).
- The agent's settings get **Advanced → Tools** (the same list as the run settings, below). The agent wizard leaves all
  tools on.

## 3. The Tools list (UI)

- Under **Advanced** in the chat's Agent settings sheet (`AgentSettingsSheet.tsx`, next to Prompt) and in the agent's
  settings.
- The catalog tools grouped as in `tools/tools.json` (Documents, Inventory, Code, Servers, Memory, Agents), each a
  checkbox with its name and one-line description; per group **All** / **None**; a line "N of 37 tools on".
- In a chat of a saved agent, the agent's off tools are shown off and disabled ("Off for this agent"); the others can be
  switched off for the next conversation.
- An open conversation's settings are read-only as today (its tools are those it started with).
- Debug lists the offered tools as today (now reflecting the selection).

## Not in scope

- Per-tool usage text overrides, per-tool permissions beyond access, MCP tool-level selection.

## Testing

- Rules: the union (agent ∪ request), unknown names refused, ad-hoc runs, subagents/experts never get an off tool back.
- Runner: an off tool is not offered and the backstop refuses it; Debug and preview match.
- API: agent profile read/write; session detail and "start new with these settings".
- Migration up/down.
- UI: the list in both places, All/None, the agent's off tools locked in a chat, the count.
