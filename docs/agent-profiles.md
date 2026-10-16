# Agent profiles

Administration → Agents lists named agents. Every agent is edited, disabled and removed
the same way; none is protected.

Create a specialist with a required name and system prompt, plus optional purpose
and “when to use” description. Every agent owns one configuration; nothing is
inherited from another agent. Agents share these tabs:

| Tab | Configuration |
| --- | --- |
| General | Name, purpose, routing description, model connection, **Instructions** (additional notes for every run), **Content editor** (off by default: "Lets this agent propose services and documents and edit editable documents."), **Answer only from documents**, Offer as an expert, availability, and the required system prompt (one field, up to 100,000 characters); *Clone of <original>* for a clone |
| Resources | The agent's resources grouped by type (servers, services, environments, code projects, collections, documents, skills, MCP servers) with name and short description. **Add** per type offers the project's resources the agent does not have yet (from `GET /api/agents/{id}/options`), with **Select all**; **Remove**; a document has an **Editable** switch (read-only by default). A reference to a resource that no longer exists shows *missing* and is dropped on save. **Save resources** writes `PUT /api/agents/{id}` with the access object |
| Test | Run the agent with all its resources, or a subset picked in the chat settings (all picked by default; a document goes with the agent's own Editable; with none picked the run cannot start: "Pick at least one resource"); continue the conversation, inspect live activity, close/resume sessions, and request tuning suggestions |

Other `?view=` values (including the former `tools`) open General.

**Access.** What an agent may use is one access object, `ka.agents.access`:
`{"resources": [{"type", "id", "editable"?}], "roles": {"content_editor": bool}}`. Types are `server`, `service`,
`environment`, `code_project`, `collection`, `document`, `skill` and `mcp_server`; only references are stored (names,
descriptions and paths are loaded at run start), `editable` applies to documents. The agent's instructions are
`ka.agents.instructions`. Every run narrows the access object: it gets all of it, or the resources the request picks
out of it, never more (see [Agent execution](agents.md)); what the run was given brings its direct children, as in
an ad-hoc run (an environment its services and servers, a service its code projects and servers, a server the services
deployed on it), also those the agent does not reference. A reference to a resource that was deleted or archived is
skipped at run start and shown as missing in the editor; saving drops it. Nothing else is stored per agent: there are
no per-kind switches, per-server instructions, skill or MCP overrides, or per-agent SSH connections; a server's own SSH
settings and its SSH-enabled flag always apply.

Specialist Test tabs run Test sessions of the selected agent with its saved configuration, pinned when the session starts.
The first message creates a session and adds `session_id` to the URL. Follow-ups
retain context until the session is closed, or closed after 24 hours without activity. Reloading
restores history and running turns. New turns keep the session’s pinned configuration; use a new session after changing the agent, or test a change on a clone.
**Add feedback** beneath a response opens an optional panel and sends the full
configuration, whole stored conversation, selected response and your feedback
to a separate reviewer. Saved suggestions include proposed prompt,
configuration or memory-note changes, with links back to the relevant editor.
Nothing is applied automatically.

**System prompt.** Each agent has exactly one system prompt, a field on the **General** tab (card *System prompt*, **Save prompt** / **Discard prompt changes**, “System prompt saved.”). It is required, nonempty and at most 100,000 characters; there is no draft or history, so to try another prompt clone the agent, change the clone and **Apply to <original>**. `PUT /api/agents/{id}/prompt` saves it with the agent's revision (a stale revision returns 409 “Agent changed. Reload before saving.”). The agent wizard saves the prompt it generates the same way. Existing prompts are preserved, including any instructions describing a former specialization; edit them on General when changing the intended purpose. “When to use” records routing guidance.

**Clones.** **Clone agent** (header menu or the All agents row button) copies the whole configuration into a new agent named `<name> (copy)`; saved SSH credentials are referenced, history is not copied, and the clone shows *Clone of <original>*. **Apply to <original>** replaces the original's changed sections with the clone's (everything except name, description and enabled); it is refused while the original is working or when the original changed since the dialog opened. See [Agent execution](agents.md#clones).

The default IDs `search`, `code`, and `operations` preserve existing integrations.
Every agent has its own configuration; no parent agent is introduced.

## Tools

Every run gets every tool: the whole tool catalog with its built-in usage text, every tool of the MCP servers in its
access, `load_skill`, `run_subagent` (except in a subagent's own run) and one tool per offered expert. There is no
Tools tab, no per-agent tool choice, no edited usage text and no "Needs …" rule (migration 0115 dropped
`ka.agent_tools` and `ka.tools.needs`). The access object decides what a tool may do: each tool checks its arguments
against the run's resources and roles and refuses the rest (for example "This document is not available in this
run."; `report_not_covered` outside a grounded run refuses "This run does not answer only from documents."). Agent
runs, run records, session pages and Test chats still show how often each tool was used. See
[Agent execution](agents.md#tools).

**Subagents.** Every run may start subagents with `run_subagent`: helpers that work on separate parts of
the task in parallel, each with every tool except `run_subagent` and the run's access narrowed to the resources it is
given, with content editor off. There is no switch for it.

**Experts.** **Offer as an expert** (General, off by default; `expert` in `GET/PUT /api/agents/{id}` and in the list,
which shows an **Expert** badge) offers the agent to every other agent as a tool. Saving it on needs a non-empty When to
use (400 "Experts need When to use."). An expert is offered while it is enabled and has a model; a caller never gets
itself, and an expert's own run calls no experts. Every run gets every offered expert; a run may narrow them in its
chat settings (`expert_ids` in the start request). There is no per-agent choice of experts.

## New agent

Agents → New agent designs a specialist from a role description. Choose
an enabled model connection, then generate a proposal with the official Agents SDK.
The designer receives the agent JSON schema and current resource metadata:
code projects and their mapped folders, collections, documents, servers,
environments, skills, MCP servers, services, model connections, and existing
agent descriptions. **Show design context** displays this input and the
generation prompt. Private keys and tokens never enter that context.

The wizard asks for **Agent model** (default: the first model in the catalog) first (page 1) and shows it on the review. The review has General (identity, instructions, the **Content editor** switch and a summary of the proposed resources), Prompt and Resources (the catalog per type, with **Editable** for picked documents) tabs. `POST /api/agents/wizard` accepts `access` (references and roles) and `instructions`; it has no tool choice (a `tools` field is refused as an unknown field).

Generation does not create an agent or execute its proposed capabilities. The
parser keeps supported configuration fields, validates their types and resource
IDs, and discards model-added annotations; the create API rejects unknown fields.
The administration proxy allows 300 seconds for generation's 240-second budget.
The designer has no code, document, SSH, skill, MCP, or service access. Review tabs
let the administrator edit identity, routing guidance, model, system prompt,
instructions, availability, the Content editor role and the resources. Servers are
reached with their own saved SSH settings.

**Create agent** validates the reviewed proposal against current available
resources and saves the agent, its prompt, settings, and joins
in one PostgreSQL transaction. Duplicate names and unavailable resources return
an error without leaving a partially configured agent. All existing agent
editing, cloning, config preview, and deletion pages work afterward.
The wizard is the only way to create agents. **Start blank** skips generation and opens
the review tabs with an empty proposal, so agents can be created without a model;
`model_id` may be `null`.

Implementation: `knowledge_admin/agent_wizard.py` defines the proposal schema,
design prompt, catalog, and three administration endpoints. It calls the existing
database-independent `agents.agent()` function with `output_schema`; no new
runtime, SDK wrapper, dependency, or migration is introduced.

- `GET /api/agents/wizard/options`: public resource metadata, schema, design prompt,
  and suggested generation model.
- `POST /api/agents/wizard/generate`: `{role, model_id}` → `{proposal}`; bounded to
  240 seconds overall, with one correction attempt for invalid model output,
  sanitized provider errors, and no database writes.
- `POST /api/agents/wizard`: reviewed proposal (`access` with references and `content_editor`, `instructions`) →
  `{id}`; atomic creation.

## Persistence and APIs

Migration `0020_agent_profiles` introduced agent identity and resource joins. Migration `0021_agent_capabilities` replaced the fixed `kind` with six independent capability switches (dropped in 0114). Migration `0022_required_agent_prompt` required a prompt record for every agent; since `0079` the prompt is `agents.system_prompt`.

Migration `0072_knowledge_agents_config` lifts the 0070 registration rule, adds `claude_code_tools` (default all four, values restricted), restores the required prompt for every agent and gives existing Claude Code agents the built-in documentation prompt; its down migration refuses while a Claude Code agent has a model, any configuration versions (or an active one), a capability switch on (including grounded documents or all collections/code projects), tools other than the default four, more than one prompt version, or server instructions, saved instructions or server connections. Otherwise it drops the other saved selections of Claude Code agents (specific collections, code projects, services and skills, settings rows) and their prompt, keeping only the name and description.

Migration `0073_remove_documentation_generation` removes the one-shot generation (tables, job kind, history and `doc:*` run records), adds `documents.phase_metadata`, and its down migration restores the empty schema only. The Claude Code Test tab no longer links to Generate documents; it says "Claude Code agents can no longer start sessions. Use a native agent."

Migration `0074_native_agent_sessions` adds `agent_sessions_cc.runtime`, `agent_config` and `history`; its down migration refuses while native sessions exist. Native agents' Generate documents sessions run in `agent-session-worker`.

Migration `0075_agent_resources` adds `agents.resources` (`fixed` | `per_run`, default `fixed`; dropped in 0114); its down migration refuses while per-run agents or per-run configuration versions exist. Migration `0076_run_resources` adds `run_resources` to `agent_sessions_cc`, `agent_sessions` and `operations_runs`; its down migration drops only these records.

Migration `0077_remove_coordinator` removes the multi-agent board and the coordinator: it deletes the board run
records (`board:*`), drops `agent_board_tasks`, `agent_board_members` and `coordinator_settings`, deletes the
coordinator agent with everything it owned, and drops `agents.is_coordinator` with the coordinator-only rules that
0031, 0032, 0072 and 0075 added. Audit events stay. Its down migration restores the empty schema only.

Migration `0078_agent_tools` creates `ka.agent_tools` (`agent_id`, `tool`, `mode` on/off/per_run, `usage`, `subagent_allowed`) and backfills every native agent with the tools its capabilities gave it, so nothing changes at upgrade; `code_graph` stays offered only when a selected code project has a generated graph. Its down migration drops the table and any edited usage text.

- `GET /api/agents` lists profiles with model summaries, `cloned_from` and `counts`: per resource type, how many of the
  resources its access object references still exist.
- `GET /api/agents/{id}` returns the agent with `instructions` and `access`: each reference resolved to `{type, id,
  name, description_short}` (a document with `editable`; a resource that no longer exists with `missing: true`) and
  `roles.content_editor`.
- `GET /api/agents/{id}/options` lists every resource of the project by type (`{type, id, name, description_short}`), for
  the Resources picker.
- `PUT /api/agents/{id}` saves metadata, `instructions` and `access` (references `{type, id, editable?}` and roles) using
  optimistic revisions. Each reference must be of a known type; one to a resource that does not exist in the project is
  dropped, and `editable` is kept for documents only.
- `POST /api/sessions` with `agent_id` accepts `resources` picked from the agent's access; the session keeps its narrowed access object.
- `PUT /api/agents/{id}/model` assigns an enabled compatible model.
- `GET /api/agents/{id}/config` returns the agent's configuration (`knowledge_admin.knowledge_agents.config.get_agent_config`).
- `PUT /api/agents/{id}/prompt` saves the system prompt; `POST /api/agents/{id}/clone`, `GET|POST /api/agents/{id}/apply` and `GET|POST /api/agents/{id}/reviews/{review_id}[/apply]` are described in [Agent execution](agents.md#clones).
- `GET /api/sessions?agent_id={id}` lists the agent's conversations, `GET /api/sessions/{id}` restores one,
  `POST /api/sessions/{id}/close` ends one and `POST /api/sessions/{id}/review` saves tuning suggestions.

Administration endpoints require the existing administrator authorization. Model,
SSH and MCP secrets never appear in profile responses. CRUD uses revision checks;
omitted replacement secrets preserve saved credentials.

`DELETE /api/agents/{id}` accepts `{"revision": 1, "confirmed": true}`. The UI
asks for confirmation. Deletion removes only the agent's row (its settings,
prompt, access object and tool rows). Clones of a deleted agent stay and lose their *Clone of* link. Shared models, skills, servers, repositories and documents
remain. An agent with sessions or run history returns HTTP 409 ("Delete it with its
history, or disable it to keep the history.") unless the body also has
`"with_history": true`.

`GET /api/agents/{id}/history` returns what that would delete:
`{"test_chats", "documentation_sessions", "run_records"}`. With
`with_history` the same transaction first deletes the agent's run records
(`agent_run_events` with keys `run:<run id>` and `cc:<session id>:<turn>` of those rows only), its runs and their SSH
commands, its sessions (Test and document runs) with their queued inputs and reviews. Saved documents are
independent rows and stay. While the agent is working — a session that is
`starting` or `working`, or a `running` operations run — the delete (with or without history) returns HTTP 409
("This agent is working right now. Stop its documentation sessions and runs, or wait for them to finish, then
delete it.") and deletes nothing. The revision is still checked; the `agent.deleted` audit event records the
deleted counts under `history`.
Migration 0025 permits atomic agent deletion and preserves the legacy model
assignment projection when a default agent is removed.

Migration `0113_agent_access` adds `agents.access` and `agents.instructions`, converted from the per-type tables (a switched-off kind left out, "all" collections or code projects expanded to the current ones, agent-level MCP servers and instructions). Migration `0114_drop_agent_resource_tables` drops the per-type selection tables, the per-server configurations and overrides, the per-agent SSH connections, the capability switches and `agents.resources`; its down migration recreates them from each agent's references (per-server overrides and SSH connections are not restored).

Migration `0079_agent_system_prompt` adds `agents.system_prompt` (text, required) filled from each agent's active prompt version, and drops the foreign key that required prompt rows. Its down migration restores the prompt rows: an agent whose prompt was edited after 0079 gets it as a new active version, and text is truncated to 40 000 characters (the old limit).

Migration `0080_clone_agents` makes each agent's active configuration version its configuration (tools, overrides and connections included), then drops `agent_versions`, the prompt tables, the view and guard function, the version column on sessions (`agent_sessions.agent_version_id`) and its constraints; it adds `agents.cloned_from` (nullable, `ON DELETE SET NULL`). Its down migration restores the empty schema only. See `deploy/README.md` for the upgrade notes.

Migration `0081_test_sessions` turns Test chats into sessions of `agent_sessions_cc` (`purpose`), adds `model_calls` and `reasoning_tokens`, moves suggestions onto it and drops the old Test chat tables (their run records stay). See `deploy/README.md`.

Migration `0084_remove_claude_code` converted Claude Code agents to native agents (the tools of their enabled capabilities are switched on; model, prompt and resources are kept), drops `agents.runtime` and `agents.claude_code_tools`, renames `agent_sessions_cc` to `agent_sessions` and drops its `runtime`, `claude_session_id` and `cost_usd` columns (`agent_config` is required). It deleted Claude Code sessions with their inputs and reviews; their run records (`cc:` keys) stay. Older audit rows keep the `claude_code.session.*` actions.

Migration `0085_agent_experts` adds `agents.expert` and lets `ka.agent_tools` hold `expert:<agent id>` rows (off only; never allowed for subagents). Its down migration drops the column and deletes those rows, so callers lose their choices of experts to switch off.
