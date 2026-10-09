# Agent experts — design

Date: 2026-10-09. Status: approved in conversation. Replaces Feature 6 (`ask_agent`) of
`2026-10-08-agent-tools-design.md`; Feature 4 (built-in agents, Code reader) and Feature 5 (extra tools per run) are
dropped from the roadmap.

## Goal

A saved native agent can be offered as an **expert**: an agent tuned for one kind of task with its own prompt,
skills, tools and resources. Any native agent, including Generate documents agents, calls an expert as a tool. A
documentation agent can split its work: an expert where one fits, otherwise a subagent (`run_subagent`) with a task
written for that part.

User decisions:
- An agent becomes an expert by a switch on its own General tab. Every expert is then offered to every other native
  agent, on by default; each caller can switch single experts off on its Tools tab.
- One tool per expert (not one `ask_expert` tool with a choice).
- Generate documents agents get every expert and `run_subagent`.

Out of scope: experts calling experts; experts for users with fewer rights (no access levels yet); choosing experts per
Generate documents session; built-in agents; extra tools per run.

## Data model (`0085_agent_experts`)

- `ka.agents.expert boolean NOT NULL DEFAULT false`.
- `ka.agent_tools` rows `expert:<agent id>` hold a caller's choice for one expert. Only `mode='off'` is stored (no
  row = on, as for MCP tools); saving an expert back on deletes the row. `subagent_allowed` is always false for them:
  the check `subagents_do_not_spawn` is recreated to cover `tool LIKE 'expert:%'` (replacing the unused
  `ask_agent:%`).
- Deleting an agent deletes every `expert:<its id>` row (in the delete transaction; the column comment and the tool
  column comment name the new form).
- **Down:** drops the column, deletes `expert:%` rows and restores the old check.

## Configuration

- **General tab** (native agents): switch **Offer as an expert**, help "Other agents can call this agent as a tool.
  Its When to use text tells them when." Saving it on requires a non-empty `when_to_use` (400 "Experts need When to
  use."). The profile (GET/PUT `/api/agents/{id}`) gains `expert: bool`; the list gains it too (badge).
- **An expert is offered** when it is an expert, enabled and has a model. A caller never gets itself.
- **Tools tab, Experts group:** every other expert with name, When to use, Fixed / Per run and a switch (on by default;
  off saves the `expert:<id>` row). Empty: "No experts yet. Turn on Offer as an expert on an agent's General tab."
  On an agent that is itself an expert: "When this agent runs as an expert it cannot call experts."
  `PUT /agents/{id}/tools` accepts `expert:<id>` entries for existing experts other than the agent (400 naming an
  unknown one) with mode `on` or `off`; usage is not editable (the description is the expert's When to use).
- **Clone** copies the caller's `expert:` rows; the clone starts with `expert=false`. **Apply to original** treats
  `expert` like the other General fields.

## Pinning

- When a session's configuration is pinned (Test session created, Generate documents session created), the experts it
  may call are pinned with it: `config['experts'] = [{id, name, tool, when_to_use, resources}]` (`resources`: `fixed`
  or `per_run`, plus for per-run experts the kinds they accept). Later changes to the expert list apply to new
  sessions. Generate documents pins every offered expert.
- The expert's own configuration is loaded at call time (its current settings).

## The tools

- **Name:** `ask_<slug>` — the expert's name lowercased, every run of other characters replaced by `_`, trimmed,
  at most 50 characters; a name that gives an empty slug uses `expert`; a slug taken by an earlier expert in the list
  (by name) gets `_2`, `_3`.
- **Description:** "Ask <name>, an expert agent. <When to use>". Per-run experts add "It works only on the resources
  you pass (it accepts: code projects, services, …)."; fixed experts add "It uses its own resources."
- **Parameters:** `task: str` (non-empty, at most 20,000 characters) and, for per-run experts only,
  `resources: {code_project_ids, collection_ids, server_ids, service_ids} | None`.
- **Checks** (tool errors the model sees):
  - the expert still exists, is an expert, is enabled and has a model ("Expert <name> is not available.");
  - each passed resource is in the caller's current lists ("<kind> <id> is not one of your resources."), then
    `run_resources.resolve` against the expert's allowed selection (its message); `None` passes nothing;
  - at most 16 subagents and expert calls per turn together ("This turn already started 16 helpers.").
- **Running** (`knowledge_agents/experts.py`, next to `subagents.py`):
  - the expert's configuration with the resolved resources (per run) or its own (fixed), its prompt, instructions,
    skills, tools and model; no history; its `experts` list emptied (no expert tools inside an expert); it keeps
    `run_subagent` if it has it, and its subagents run under the same turn limits;
  - credentials: a fixed expert's are loaded for the call (`load_credentials`) and added to the turn's redaction
    secrets; a per-run expert uses the caller's already decrypted credentials for the passed resources only;
  - runs under the turn's subagent semaphore (`AGENT_MAX_SUBAGENTS`, default 4) and is cancelled by Stop like a
    subagent;
  - returns the final output, redacted; a failure returns "Expert <name> failed: <redacted message>".
- **Subagents** never get expert tools (`subagent_allowed` false).

## Recording

- Expert events go into the caller's turn record (`cc:<session>:<turn>`) as subagent events do: `parent_call_id` = the
  call id; the first event `input {task, parent_call_id, expert: {id, name}}`; every event's `agent_id` is the
  expert's id. Usage goes through the session's `add_usage`, so totals and tool counts include expert work.
- **The expert's history:** `GET /api/agent-runs?agent_id=<expert>` also returns the calls made to it (kind
  `expert_call`: time, caller agent, task, status, tokens, run key and call id). Opening one shows the caller's record
  narrowed to that call's events (`GET /api/agent-runs/{run_key}?call=<call id>`). Events are not copied; tokens are
  counted once, in the caller's run.

## Generate documents

- `ad_hoc_config` no longer excludes `run_subagent` and pins every offered expert.
- The default system prompt (`DEFAULT_SYSTEM_PROMPT`, editable in the form) adds:
  > When the document covers several parts (services, code projects, servers), plan the sections first. For each
  > part, ask an expert when its description fits; otherwise start a subagent with run_subagent. Give each a complete
  > task: the section's purpose, what to cover, and only that part's resources. Write small single-part documents
  > yourself. Combine the answers into one consistent document; check that they agree and resolve or mark conflicts.
- The subagent prompt (`prompts/subagent.md`) adds: "When asked for a section, return it as finished markdown with its
  evidence cited."
- Sessions created before this change keep their pinned configuration (no experts, no `run_subagent`).

## Run workspace

- `groupTurns` groups expert events like subagent events (by `parent_call_id`), marking the entry as an expert with
  its name.
- Sidebar: "Expert · <name> · <task>" with status, tool calls and tokens, live; clicking shows its history in the main
  pane with the breadcrumb "Main agent › <name>: <task>".
- Worked block: "Asked <name> · <task> · 12 tool calls · 18k tokens · done" with **Open**.
- All agents: an **Expert** badge. Agent runs: `expert_call` rows labelled "Called as expert by <caller>".

## Errors

| Problem | Behaviour |
| --- | --- |
| Expert switch on with empty When to use | 400 "Experts need When to use." |
| Expert switch on a non-native agent | 400 with today's native-only message |
| Tools PUT naming an unknown expert or the agent itself | 400 naming it |
| Expert disabled, deleted, no longer an expert or without a model | Tool error "Expert <name> is not available." |
| Resource not the caller's / not allowed for the expert | Tool error naming it |
| More than 16 helpers in one turn | Tool error "This turn already started 16 helpers." |
| Expert run fails | Tool result "Expert <name> failed: …" (redacted); the caller continues |

## Testing

- **Migration:** column, `expert:` rows, the check (expert rows cannot be allowed for subagents), delete cleanup,
  up/down, rollback pin.
- **Configuration:** switch round-trip, When to use required, native only; Tools tab rows (default on, off row,
  back on deletes it, unknown or self refused); clone copies rows and starts with `expert=false`; Apply to original.
- **Pinning:** offered experts pinned (enabled, model, not self, not switched off); Generate documents pins all;
  older pinned sessions unchanged.
- **Tools:** names and collisions, descriptions, schemas for fixed and per-run experts; every check above.
- **Running:** per-run resources narrowed and resolved; fixed expert uses its own resources and credentials, which
  are redacted; no expert tools inside an expert; shared limit and semaphore; Stop cancels; failure result.
- **Recording:** `parent_call_id`, expert `agent_id`, totals; expert calls list and the narrowed record.
- **Generate documents:** `run_subagent` and experts offered; default prompt and subagent prompt texts.
- **UI:** General switch and validation; Experts group, empty and self notes; badge; `groupTurns` expert grouping;
  sidebar and Worked block entries; Agent runs rows.
