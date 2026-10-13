# Agent execution status

The agent **Test** tab runs **Test sessions**: sessions in `ka.agent_sessions` with `purpose = 'test'`, run by
`agent-session-worker` exactly like Generate documents sessions (claim and lease, turns, comments delivered
between model calls, Stop with a 30-second grace, closing after 24 hours without activity, the worker's concurrency
limit, no per-turn time limit, no 300-second limit).

- `POST /api/sessions` with `agent_id` and `message` creates a Test session: the agent's current configuration is
  pinned in `agent_config` with credential references only, the title is the first 200 characters of the message, and
  the message is queued as input 1 (201 with the session). A disabled agent and an agent without an enabled model
  (`Select an enabled model in this agent’s General tab.`) are refused. A saved credential that cannot be decrypted
  does not refuse the start: the first turn fails with the worker's message.
- **A Test run can only narrow the saved agent.** The request can list the tools it keeps (`tools`; the others are
  switched off), choose some of the agent's experts (`expert_ids`), turn subagents off (`subagents: false`) and, for a
  per-run agent, choose from its saved resources (`resources`). Nothing can be added. The model and prompt are the
  agent's own: change them in General, or clone the agent to try another prompt. Refused: "{tool} is not one of this
  agent's tools.", "This agent does not allow subagents.", "A saved agent uses its own model and prompt.", "A saved
  agent takes no environments; choose its servers or services.", "A saved agent takes no documents yet." and resources
  outside the agent's selection. A fixed agent given resources is refused.
- `GET /api/sessions/options?agent_id=…` says what may be narrowed (tools, experts, whether subagents are allowed, a
  per-run agent's resources) and gives the agent's model and prompt as facts. `POST /api/sessions/preview` with the
  same body shows the pinned configuration without starting a run.
- `GET /api/sessions?agent_id=…` lists the agent's Test sessions and its event sessions (with `purpose`), newest first.
- Everything else is the session API: `GET /api/sessions/{id}` (with `purpose`, `model_calls`,
  `reasoning_tokens` and, for Test and (pre-0090) event sessions, `config` — the pinned configuration — and
  `reviews`), `POST …/messages` (Send works while the agent works), `POST …/stop`, `POST …/close`, `GET …/events/history` and `GET …/events`.
  `GET /api/sessions` lists runs (filters `agent_id`, `purpose`, `document_id`, `offset`); `GET /api/sessions/options` and
  `POST /api/sessions/preview` describe and preview a run, and `POST /api/sessions` starts it (Ask agent and the
  agent's Test tab use it).
- Document tools: `get_document(document_id, section?, start_line?, end_line?)` reads the run's own draft of a document
  when it has one, and works for the run's documents and, read-only, documents attached to inventory entries.
  `edit_document(document_id, old_text, new_text)` and `save_document(document_id, markdown, title?, description?)`
  work only on documents given to the run as editable, and write the run's draft, never the live version. Every
  per-resource tool checks its argument against the run's pinned allowlist ("This document is not available in this
  run."). The editing rules are the **Revise a document** prompt in Administration → Prompts, which you can open and change.
- Event handlers (Administration → Events → Handlers, action Send to agent) do not start sessions: since migration
  0090 they queue an agent task (`ka.agent_tasks`), and agent-session-worker runs it once with the rendered prompt —
  `agent(config, prompt, stream=False)`, the agent's current configuration, no history, no experts and no
  `run_subagent` (agent() offers them only to a session turn), a 10-minute limit — and writes the answer or the
  failure to `ka.notifications` (Administration → Notifications). `AGENT_TASK_MAX` (a whole number of at least 1,
  default 2) tasks run at once, beside the session slots; an invalid value is logged every 5 seconds (by the
  supervisor) and the task runner stays down until the value is fixed, while sessions keep working. The worker runs
  the session loop and the task loop under supervisors: a loop that crashes is logged and restarted after 5 s, and the
  other keeps running. A task is tried up to 3 times (30 s, then 2 min apart); a worker that stops hands a running
  task back without counting the attempt. Each pickup of a task is recorded as run key `task:<task id>:<claim
  number>`, kind `event_task` (**Event task** in Agent runs), so a pickup after a worker restart has its own record.
  Sessions with `purpose = 'event'` created before 0090 stay viewable in the agent's Test tab (**Event** badge).
- Each turn's run record is `cc:<session>:<turn>` (start kind `test_session`, or
  `event_session` for the pre-0090 event sessions); **Full record** links to it. The `end`
  event of a completed grounded turn carries `grounding` (cited sources and coverage, redacted). 
- `POST /api/sessions/{id}/review` `{ "comment": "...", "turn": n? }` (Test and pre-0090 event sessions;
  refused while the agent works) builds the conversation from the session's events — per turn the message, comments sent while
  the agent worked, the tools called (name and summary) and the final answer or error — and runs the tuning reviewer
  (the session's model with every capability off, 240 seconds) with the pinned configuration. The suggestion is stored
  in `ka.agent_session_reviews` with `response_turn`; **Review and apply** reads it.

The Test tab and the Generate documents session page share `RunWorkspace` (`services/admin-ui/app/components`):
a header (agent, type, resources, status, tokens, tools used), turns with a collapsed **Worked** block and the answer
with its sources, a sidebar **Agents in this run** (a drawer on narrow screens) with the page's sections, and a message
box that stays on screen. `useSessionEvents` loads the latest page, earlier pages and the live stream (fed by the admin-api session hub, see
[documentation](documentation.md)); `groupTurns`
turns events into turns. An `ask_<slug>` call shows in the Worked block as `Asked <name> · <task> · <n> tool calls · <tokens>
tokens · <status>` with **Open**, and the sidebar lists `Expert · <name> · <task>` (breadcrumb **Main agent › <name>: <task>**);
an expert entry appears only once the expert has started (its first event), so a call refused before starting shows as a
failed tool line. A `run_subagent` call shows in the Worked block as `Subagent · <task> · <n> tool calls ·
<tokens> tokens · <status>` with **Open**; **Agents in this run** lists the main agent and every subagent (working,
done, failed or stopped, with tokens and tool calls, live), and selecting one shows its own history in the main pane
with the breadcrumb **Main agent › Subagent: <task>**. `groupTurns` puts events with a `parent_call_id` into the
turn's subagents. The sidebar lists the subagents in the loaded events, so earlier ones appear when older history
loads. The header and the main agent's sidebar entry show the session totals including subagents; the main turn's
Worked line counts only its own work.

Test chats from before migration 0081 were deleted with their tables; their run records (`run:<id>`) stay in Agent runs.

### Grounded answers

With Documents on and **Answer only from documents** on, an agent reads its granted
collections through `search_documents` and `read_document` (no file/shell tools for
documents), cites claims with `[n]`, and calls `report_not_covered` to mark uncovered
questions **not covered**. Try it in the agent's **Test** tab.
Migration 0071 removed the former Reader application's conversations and its
`ka.reader_settings` and two columns of the old Test chat table.

**Upgrading.** Migration 0060 turns **Answer only from documents** on and Code
(repositories) off for the Search agent; its down migration does not turn Code back
on. Saving the agent's settings changes what runs next; conversations already started keep the configuration they began with.

The former code-agent service and its "Ask the code" escalation were
removed; agents read code with their own tools (`list_files`, `read_file`,
`code_search`, `ast_grep`, `code_graph`).

`knowledge_admin.agents.agent()` runs a configured specialist with the Agents SDK.
The caller provides a saved config, task, history, and decrypted credentials. Responses and Chat Completions
connections use the corresponding SDK model classes; native Anthropic/Bedrock uses
`LitellmModel`. See [the runtime documentation](../services/admin-api/README.md).

Git synchronization, Jira import/export, RAG processing/search and connection tests
are functional integrations. Connection-test buttons may contact their configured
server or model. Keycloak authentication and Phoenix infrastructure remain.

See [agent configuration](agent-profiles.md), [SSH settings](server-operations.md),
[LLM connections](llm-settings.md), and [PostgreSQL migrations](migrations.md).


## Tools

Tools are part of an agent's configuration (`config['tools']`, edited on the Tools tab, see
[agent configuration](agent-profiles.md)). Conversations saved before migration 0078 use the tools
their capabilities gave. The model gets exactly the selected tools whose needs are met; MCP tools are filtered the
same way and edited MCP usage is added to the prompt as `Tool notes:`. `code_graph` is offered when a selected code
project has a current generated graph (`code_graph` results carry its `generated_at`; if the project was synced after
the graph was generated, the answer carries a note to regenerate it or confirm with `read_file`).

Tool usage per run is recorded as counts: the **Tools** column of Agent runs (top three and `+n`, the tooltip lists
all), the run record, the session page and the Test tab show a line such as `code_graph 12 · read_file 31 · shell_run 4`
or `No tools used`. The workspace counts live: the session detail returns its counts with the id of the
last tool call they include, and the page adds the streamed tool calls after it (a replay after a reconnect counts
once; earlier activity loaded by scrolling up is already counted).

**Subagents (`run_subagent`).** An agent with **May start subagents** gets `run_subagent(task, tools, resources)`. A
subagent works on one task (at most 20,000 characters) without the conversation: its configuration is a copy of the
agent's pinned configuration with only the given tools on (names as the agent sees them; MCP servers kept only for the
given MCP tools, their other tools off; never `run_subagent`, never a tool not allowed for subagents), the resource
lists narrowed to the given ids (each must be in the agent's current lists; given resources make it a per-run run, so
an empty list gives nothing), the built-in prompt `knowledge_agents/prompts/subagent.md`, no instructions, skills off
and the same model. It gets only the turn's already decrypted credentials its configuration references (SSH private
keys only with `ssh_run` or a kept SSH-hosted MCP server) and shares the turn's scratch workspace. A turn starts at
most 16 helpers, subagents and expert calls together ("This turn already started 16 helpers."), and runs
`AGENT_MAX_SUBAGENTS` (default 4) at once, shared with expert calls; later calls wait. The result is the subagent's final answer, redacted, or `Subagent failed: <reason>`; refusals are tool
errors naming the tool or resource. Subagents run only in native agent sessions (Test and Generate documents). Their
events go into the turn's record (`cc:<session>:<turn>`) with `payload.parent_call_id` (the `run_subagent` call id; the
first is `input {task, subagent}`), their model calls into the session's totals and their tool calls into the tool
counts. **Stop** stops the agent and every subagent; a subagent of an ended turn starts no model or tool call.

**Experts (`ask_<slug>`).** A native agent, including a Generate documents agent, gets one tool per offered expert
(see [agent configuration](agent-profiles.md)): an agent with **Offer as an expert** that is enabled, has a model and
is not the caller, and that the caller has not switched off. The name is `ask_` plus the expert's name lowercased with
every run of characters other than `a-z0-9` replaced by `_`, trimmed of `_`, at most 50 characters (an empty slug gives
`expert`; a slug taken by an earlier expert, ordered by name and id, gets `_2`, `_3`, ...). The description is `Ask <name>, an
expert agent. <When to use>` plus, for a per-run expert, ` It works only on the resources you pass (it accepts: code
projects, document collections, servers, services).` (only the kinds it uses) or, for a fixed one, ` It uses its own
resources.` Parameters: `task` (non-empty, at most 20,000 characters) and, for per-run experts only, `resources`
(`code_project_ids`, `collection_ids`, `server_ids`, `service_ids`). The experts a session may call are pinned with its
configuration (`config['experts']`: id, name, tool, When to use, resources); later changes to the expert list apply to
new sessions, and Generate documents pins every offered expert. The expert's own configuration is loaded when it is
called. Refusals are tool errors: `Expert <name> is not available.` (deleted, no longer an expert, disabled or without
a model), `<kind> <id> is not one of your resources.`, the expert's own resource message, the 16-helper limit, an empty
or oversized task; `Unknown expert: <tool>.` for a tool the session did not pin. A failure returns `Expert <name>
failed: <reason>` (redacted) and the caller goes on.

A call runs `knowledge_agents/experts.py`: the expert's prompt, instructions, skills, tools and model with the resolved
resources (per run) or its own (fixed), no conversation, and no expert tools (an expert cannot call experts). It keeps
`run_subagent` if it has it: its subagents run in slots of their own (`AGENT_MAX_SUBAGENTS`), count towards the turn's
16 helpers and are cancelled with the call or by **Stop**. So a turn can run up to `AGENT_MAX_SUBAGENTS` x (1 + running
experts) helpers at once. The call loads its own credentials (the expert's model key and the keys of the resources it
runs with; `load_credentials` also refuses a disabled model, server or MCP server); they are added to the turn's
redaction secrets, so the caller's record shows them redacted, and cleared when the call ends. The call takes a slot
like a subagent. Subagents never get expert tools.

An expert call is recorded in the caller's turn record like a subagent: events carry `payload.parent_call_id` (the `ask_`
call id), every event's `agent_id` is the expert's, and the first is `input {task, parent_call_id, expert: {id, name}}`;
model calls go into the session's totals and tool calls into the tool counts. `GET /api/agent-runs?agent_id=<expert>`
also lists the calls made to the expert (kind `expert_call`: time, caller, task, status, tokens, run key and call id;
served by the partial index `agent_run_events_expert_calls`), and `GET /api/agent-runs/{run_key}?call=<call id>` returns
the caller's record narrowed to that call, including the expert's own subagents. Events are not copied; tokens are
counted once, in the caller's run.

## Clones

An agent has **one configuration**: what its tabs show is what runs. There are no configuration versions, drafts or
activation. To try a change, work on a clone: clone the agent, change and test the clone, then apply it to the
original, keep it as an alternative, or delete it. A conversation pins the agent's configuration when it starts, so
saving the agent never changes a conversation in progress.

**Clone agent** (the header menu *Actions for <name>* on the agent page, or the **Clone <name>** button in the All
agents list) creates a new agent with an identical configuration: prompt, instructions, model, type, resources mode,
tools with their usage text, skills, documents, code projects, services, SSH servers (per-server instructions and
connection overrides) and MCP selections. The name is `<name> (copy)`, then `(copy 2)` and so on while taken. Saved
SSH credentials are **referenced**, not copied or decrypted. Sessions, runs, run records, reviews and audit history
are not copied. The clone records `cloned_from` and shows *Clone of <original>* on its General tab and in the list.

- `POST /api/agents/{id}/clone`: creates the clone and returns it (409 `An agent with this name already exists.`
  if a concurrent clone took the name).
- `GET /api/agents/{id}/apply`: for a clone, the sections that differ from its original (`Prompt`, `Instructions`,
  `When to use`, `Model`, `Resources`, `Tools`, `Skills`, `Documents`, `Code projects`, `SSH`, `MCP`,
  `Services`), the original's revision and a fingerprint of its configuration. 404 `This agent is not a clone.` when
  the agent is not a clone or the original was deleted.
- `POST /api/agents/{id}/apply`: **Apply to <original>** replaces the original's configuration with the clone's in
  one transaction. Everything is copied except name, description, `enabled` and `cloned_from`. It needs the revision
  and fingerprint from the `GET`; 409 `The original changed; review again.` if either moved, and 409 `The original
  agent is working right now. Wait for its documentation sessions and runs to finish, then apply.` while the original
  has a documentation session or run working. It bumps the original's revision and writes the audit event
  `agent.config.applied`. The clone then offers **Delete this clone**.

Saving SSH, per-server or MCP settings locks the agent for the save, so it is serialized with Apply and with other
saves.

**Add feedback → Suggest improvements** stays. **Review and apply** on a suggestion opens *Apply suggestions*: the
proposed instruction and prompt replacements can be edited, then applied with **Apply to this agent** (new
conversations use it) or **Apply to a new clone** (clone first, then write them there).

- `GET /api/agents/{id}/reviews/{review_id}`: the suggestion with the agent's current texts and its fingerprint (404
  `Suggestion not found for this agent.`).
- `POST /api/agents/{id}/reviews/{review_id}/apply`: `{target: 'agent' | 'clone', revision, fingerprint, changes}`; 409 `The agent changed since
  you opened the suggestion. Review it again.`, 422 `Instructions refer to a server outside this agent.`, 400 `Enter a
  system prompt.`

Snapshots of running conversations store credential references, never private keys or provider tokens. Credentials
are resolved at execution time; disabled agents/models/SSH/MCP resources still block use. File contents and external
server state are live; a removed credential or moved folder can make an old conversation unavailable.

Migrations `0079_agent_system_prompt` and `0080_clone_agents` removed agent versions; see
[PostgreSQL migrations](migrations.md) and the upgrade notes in `deploy/README.md`.
