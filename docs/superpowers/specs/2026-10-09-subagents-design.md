# Subagents (`run_subagent`) and tool defaults — design

Date: 2026-10-09. Status: approved in conversation. Step 2b of `2026-10-08-agent-tools-design.md` (Feature 3),
updated for the removal of agent versions and for Test chats running in the session worker
(`2026-10-08-run-workspace-design.md`).

## Goal

A native agent can start **subagents**: helpers with a task, a subset of its tools and resources, and no view of the
conversation. They run in parallel, their work is recorded with the turn, and the run workspace shows each one in the
sidebar with its own history. In the same step, tool defaults change so that agents get the tools their capabilities
need without manual switching.

User decisions:
- **May start subagents** is a separate switch on the agent (General tab). When it is on, `run_subagent` appears in
  the agent's tool list; its description (the usage text the model gets) says when to start subagents and is editable.
- Per-run agents get all tools of the kinds they accept by default; for fixed agents, switching a capability on
  switches all of its tools on.
- Stop stops the main agent and every subagent (no per-subagent Stop). At most 16 subagents per turn.

Out of scope: `ask_agent`, extra tools per run, a different model for subagents, per-subagent Stop or messages.

## Tool defaults

- **"A capability's tools"** = the catalog tools whose `needs` belong to it (code: files, code, code_graph; documents:
  documents, grounded, plus files when no code projects; SSH: ssh), as `capability_defaults` groups them today, but
  **all** of them, not only those whose `default_on` holds. `report_not_covered` stays tied to "Answer only from
  documents" (on exactly when grounded, as today).
- **Fixed agents:** switching a capability on (General or the wizard) switches all of its tools on, replacing any
  earlier off rows for them (while the capability was off they had no effect). Switching it off keeps the rows.
- **Per-run agents:** a new per-run agent, an agent switched to per run, and a capability switched on while per run
  get all tools of every enabled capability.
- **Existing agents are not changed** by this step; their saved tool rows stay as they are. The Tools tab still lets
  anyone switch single tools off.
- Native only; Claude Code agents are unaffected.

## The switch: May start subagents

- **General tab:** "May start subagents", next to Agent type and Resources, with one line of help ("The agent can hand
  separate parts of its work to helpers that run in parallel."). Native agents only; off by default (also in the
  wizard, which asks the same on its Tools page).
- **Storage:** the switch is the `run_subagent` row in `ka.agent_tools` (`mode='on'`, `subagent_allowed=false`, as the
  0078 check `subagents_do_not_spawn` requires). On inserts the row (usage NULL = default text); off deletes it. No
  migration and no new column.
- **API:** the agent profile (GET/PUT `/api/agents/{id}`) gains `subagents: bool`; the PUT writes or deletes the row,
  under the agent-row lock, bumping the revision like other configuration saves. `PUT /agents/{id}/tools` accepts a
  `run_subagent` entry only while the switch is on (400 "Turn on May start subagents in General first.") and keeps
  `mode='on'` for it; switching it off happens only through the switch.
- **`subagent_allowed`** is now stored from the Tools tab save (it was ignored); it is always false for
  `run_subagent`. Default true for every other tool.
- Clone and Apply to original copy it with the other tool rows (already the case).

## Tools tab and wizard

- When the switch is on, `run_subagent` is listed in the **Agents** group with its label ("Start subagents"), its
  usage text (editable, **Reset** to the default) and, under it, **A subagent may use**: one checkbox per tool of the
  agent (catalog tools and MCP tools that are on), checked by default; `run_subagent` itself is never listed.
- When the switch is off, the Agents group shows "Turn on May start subagents in General to let this agent start
  helpers."
- The wizard's Tools page shows the switch and, when on, the same checklist.
- **Default usage text** (the tool's description):
  > Start a helper for a separate, well-defined part of your work, such as exploring one project or checking one
  > question. Give it a complete task, because it does not see this conversation. Give it the fewest tools and
  > resources it needs. Helpers may run in parallel. You get their final answer as the result; verify important
  > claims.

## The tool

- **Catalog entry** `run_subagent` (group `agents`, needs: none — offered exactly when its row is on, native only).
- **Signature:** `run_subagent(task: str, tools: list[str], resources: {code_project_ids, collection_ids, server_ids,
  service_ids} | None)`.
- **Checks** (refusals are tool errors the model sees, no exceptions escape):
  - `task` non-empty, at most 20,000 characters;
  - `tools` non-empty; each name is offered to this run (`context['tools']`, MCP tools as the model sees them) and
    has `subagent_allowed`; never `run_subagent`;
  - each resource id is in the parent's current configuration lists (already narrowed for per-run agents); `None`
    gives the parent's lists;
  - at most **16** subagents per turn ("This turn already started 16 subagents.").

## Running a subagent (`knowledge_agents/subagents.py`)

- **Sub-config:** a deep copy of the parent's effective configuration with resource lists narrowed to `resources`
  (as `run_resources.resolve` narrows, including `code.graphs`), `tools` = only the given tools `on` (MCP: servers
  kept only for given MCP tools, other tools of those servers blocked), the system prompt replaced by the built-in
  `knowledge_agents/prompts/subagent.md`, instructions empty, skills off, same model, no `run_subagent`.
- **Subagent prompt** (`subagent.md`):
  > You are a helper started by another agent for one task. Work only on that task with the tools and resources you
  > were given. Read before you conclude; cite evidence as file:line, document path or command. If the task cannot be
  > done with what you have, say what is missing. Finish with a concise answer for the agent that started you.
- **Credentials:** taken from the parent turn's already decrypted map, only those the sub-config references; redaction
  uses the parent's secrets list. Nothing new is decrypted.
- **Run:** `agents.agent(sub_config, task, …)` with the parent's scratch workspace, no history, `max_turns=None`, no
  time limit, the parent's model-input filter for closed turns (no comments are delivered to subagents), and
  `SubagentEvents` hooks. Returns the final output, redacted, as the tool result; a failure returns "Subagent failed:
  <redacted message>".
- **Parallel limit:** an `asyncio.Semaphore` per turn, `AGENT_MAX_SUBAGENTS` (default 4); further calls wait.
- **Stop:** cancelling the turn cancels the tool coroutine and the subagent's run; once the parent's events are closed,
  the subagent's hooks refuse new tool calls and the filter refuses new model calls, as for the main agent.

## Recording

- Subagent events go into the **same turn record** (`cc:<session>:<turn>`) through the parent's recorder, with
  `payload.parent_call_id` = the `run_subagent` call id and `payload.subagent` = `{task}` on its first event (`start`
  kind is not used; the first event is `input {task, parent_call_id}`). Kinds unchanged: `input`, `reasoning`,
  `message`, `tool_call`, `tool_result`, `usage`, `error`.
- Usage goes through the session's `add_usage`, so session totals (tokens, model calls, reasoning) include subagents.
  Session tool counts count every `tool_call`, so they include subagents' calls.
- No new tables; Agent runs and Full record show everything in seq order.

## Run workspace

- **`groupTurns`** puts events with a `parent_call_id` into a **subagent** of their turn (keyed by the call id), not
  into the main work block. Each subagent: task, its work items, answer, status (working until the parent's
  `run_subagent` result arrives; done; failed when the result is an error; stopped when the turn stopped first),
  tokens and tool calls.
- **Main pane:** in the Worked block, a `run_subagent` call shows one line "Subagent · <task> · 14 tool calls · 23k
  tokens · done" with **Open**.
- **Sidebar "Agents in this run":** the main agent, then every subagent of the session (newest turn last), each with
  task (truncated), status, tokens and tool calls, updating live. Clicking selects it.
- **Selected subagent:** the main pane shows its own history (input = task, Worked block, answer), live while it runs,
  with a breadcrumb "Main agent › Subagent: <task>" back. The message box still talks to the main agent.
- Narrow screens: the drawer has the same list.

## Errors

| Problem | Behaviour |
| --- | --- |
| Tools PUT with `run_subagent` while the switch is off | 400 "Turn on May start subagents in General first." |
| Switch on a Claude Code agent | 400 with today's native-only message |
| Tool not offered / not allowed for subagents / `run_subagent` | Tool error naming the tool |
| Resource not in the parent's lists | Tool error naming it |
| More than 16 in one turn | Tool error "This turn already started 16 subagents." |
| Subagent fails | Tool result "Subagent failed: …" (redacted); the parent continues |

## Testing

- **Tool defaults:** capability switched on (fixed) turns on all its tools, replacing off rows; per-run creation,
  switch to per run, capability on while per run; existing agents untouched; grounded rule unchanged.
- **Switch:** profile `subagents` round-trip, row inserted/deleted, revision bump, native only; tools PUT refuses
  `run_subagent` while off, keeps usage, stores `subagent_allowed`; clone copies it.
- **Tool:** every check above; narrowed sub-config (resources, tools, MCP blocking, prompt, no skills, no
  `run_subagent`); credentials limited to references; redacted result and failures.
- **Run:** parallel subagents with the semaphore (5th waits); Stop cancels running subagents; closed turn refuses new
  calls; usage added to session totals; events recorded with `parent_call_id`.
- **UI:** `groupTurns` subagent grouping and statuses; Worked block line with Open; sidebar entries live; switching the
  main pane and back; General switch; Tools tab Agents group, checklist and Reset; wizard switch.
