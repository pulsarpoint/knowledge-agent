# Agent profiles

Administration → Agents lists named agents. Every agent is edited, disabled and removed
the same way; none is protected.

Create a specialist with a required name and system prompt, plus optional purpose
and “when to use” description. Every agent owns one configuration; nothing is
inherited from another agent. Agents share these tabs:

| Tab | Configuration |
| --- | --- |
| General | Name, purpose, routing description, availability, Resources (Fixed / Per run), model connection, additional instructions and the required system prompt (one field, up to 100,000 characters); *Clone of <original>* for a clone |
| Skills | Enable skills; select one or more predefined skills from the library |
| Documents | Enable document search; choose folders and whether to include descendants |
| Git paths | Enable read-only Git access; choose repositories |
| SSH | Enable SSH access; choose multiple servers, optional SSH connection overrides, and per-server instructions, skills and MCP selections |
| MCP | Enable MCP tools; choose this agent’s HTTP connections |
| Tools | Choose which tools the agent gets, edit their usage instructions (native agents) |
| Services | Enable context from selected service descriptions |
| Test | Continue a Agents SDK conversation, inspect live activity, close/resume sessions, and request tuning suggestions |

Capabilities are independent of the agent name. Code can enable SSH; SSH can enable Git or document search. Switching a capability off preserves its saved selections. Save the capability switch before editing its dependent SSH or MCP settings. The Skills tab saves its switch and selected skills together. New profiles start with no model and all capabilities off. Existing profiles keep their prior access and prompts.

**Resources.** A fixed agent always uses its selections. A per-run agent's selections are what a run may be given (All = any); each run passes `code_project_ids`, `collection_ids`, `server_ids` and `service_ids`, validated against them and narrowed into the run's configuration (`knowledge_agents/run_resources.py`); skills and MCP stay fixed.

An empty resource selection includes nothing. “All document folders” and “All Git repositories” are explicit options. Services provide descriptive context; for a fixed agent they do not grant access to their servers or repositories. For a per-run agent, a service chosen for a run also brings its code projects and SSH servers into that run when the agent uses code or SSH; each must be within its selections. Skills are administrator-managed instructions. Server-specific instructions and skill/MCP selections are configured within SSH. A server without its own configuration uses the same agent’s instructions and selections. No configuration is inherited from another agent. SSH defaults remain on the shared server inventory. Each agent can override the complete SSH connection for a server; removing that override restores the server defaults. The server’s SSH-enabled flag is always enforced.

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

The **Tools** tab lists the tools of a native agent in groups (Code, Documents, Servers, MCP, Agents; empty groups are
hidden, except Agents), each with an on/off switch, a one-line summary and its usage instructions. **Edit usage** replaces the
built-in instructions the model reads with your own text (up to 8,000 characters; the row shows **Edited**);
**Reset** returns to the built-in text. A tool whose capability is not available shows a "Needs …" row instead of
a switch effect: `Needs code projects`, `Needs documents`, `Needs "Answer only from documents"`, `Needs SSH servers`,
`Needs MCP`; it stays saved but is not offered until the need is met. Capability tabs (native) say
"Tools for these projects are chosen on the Tools tab." MCP tools of the selected servers are on by default; an
edited usage text for one is added to the prompt as **Tool notes** (the tool's own description still comes from the
server). **Save tools** saves the whole list with the agent's revision (`GET/PUT /api/agents/{id}/tools`; a stale
list returns 409 "The tools changed since you opened them. Reload.").

Save rules: when **Answer only from documents** is on, `report_not_covered` cannot be switched off ("Grounded answers
need report_not_covered."); switching a capability on switches all
of its tools on (replacing earlier off rows; `report_not_covered` only with **Answer only from documents**; Documents
bring the file tools only without code projects and without **Answer only from documents**); a per-run
agent gets all tools of every enabled capability when it is created, switched to per run or gets a capability switched
on; agents saved before keep their tool rows. The wizard's explicit tool choice wins over these defaults (a per-run
agent created with a hand-picked list gets just that list). Tools of a server that is not selected, or unknown tools,
are refused. Claude Code agents keep their four tools
(Read, Grep, Glob, Bash) on the General tab; the Tools tab shows "Claude Code agents use Claude Code's own tools on
their working folders. Switch them on the General tab." and the API refuses tool changes for them. The agent wizard
has a **Tools** page that follows the chosen capabilities, and **All agents** has a **Tools** column
(for example `7 tools · Code 6 · Servers 1`, `No tools`, or `Read, Grep, Glob, Bash` for Claude Code). Tools are part
of the configuration; conversations saved before 0078 use the tools their capabilities gave.

**Subagents.** **May start subagents** (General, native agents only, off by default; the wizard asks the same on its
Tools page) lets the agent hand separate parts of its work to helpers that run in parallel. The switch is the
`run_subagent` row in `ka.agent_tools` (`mode = 'on'`, `subagent_allowed = false`; `GET/PUT /api/agents/{id}`
`subagents`); turning it off deletes the row. While it is on, the Tools tab's **Agents** group lists **Start subagents**
(its switch follows General; **Edit usage** / **Reset** change the instructions the model reads about when to start
subagents) and **A subagent may use**: one checkbox per tool of the agent that is on (catalog and MCP tools), checked by
default (`subagent_allowed`). While it is off, the group says "Turn on May start subagents in General to let this
agent start helpers." and the Tools API refuses `run_subagent` ("Turn on May start subagents in General first.").
Clone and Apply copy the row and `subagent_allowed` with the other tool rows. Switching an agent to Claude Code
deletes the row (server side, whatever the save sends), so after switching back to native the switch is off and must be
turned on again.

## New agent

Agents → New agent designs a specialist from a role description. Choose
an enabled model connection, then generate a proposal with the official Agents SDK.
The designer receives the agent JSON schema and current resource metadata:
code projects and their mapped folders, document folders, SSH servers and saved
credential references, skills, MCP servers, services, model connections, and
existing agent descriptions. **Show design context** displays this input and the
generation prompt. Private keys and tokens never enter that context.

The wizard asks for **Agent type** and **Resources** first (page 1) and shows them as one line on the review. `POST /api/agents/wizard/generate` accepts `runtime`: the designer's catalog then lists only models that fit that type, and a proposed model that does not fit is returned as `model_id: null`. The proposal schema itself does not include the type. `POST /api/agents/wizard` accepts `runtime`, `resources` (default `fixed`) and `claude_code_tools`.

Generation does not create an agent or execute its proposed capabilities. The
parser keeps supported configuration fields, validates their types and resource
IDs, and discards model-added annotations; the create API rejects unknown fields.
The administration proxy allows 300 seconds for generation's 240-second budget.
The designer has no code, document, SSH, skill, MCP, or service access. Review tabs
let the administrator edit identity, routing guidance, model, system prompt,
instructions, availability, all six capabilities, resource selections, and
per-server instructions, skills, MCP selections, and SSH connection overrides.
Connection overrides can reference an existing encrypted private key. New key
material is added through the ordinary SSH editor after creating the agent.

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
- `POST /api/agents/wizard`: reviewed proposal → `{id}`; atomic creation.

## Persistence and APIs

Migration `0020_agent_profiles` introduced agent identity and resource joins. Migration `0021_agent_capabilities` replaces the fixed `kind` with six independent boolean fields: `documents_enabled`, `repositories_enabled`, `ssh_enabled`, `skills_enabled`, `mcp_enabled`, and `services_enabled`. Existing profiles receive the capabilities their previous runtime supported. Prompts, models, server overrides, encrypted credentials, resource selections and run history remain intact. Migration `0022_required_agent_prompt` required a prompt record for every agent; since `0079` the prompt is `agents.system_prompt`.

Migration `0072_knowledge_agents_config` lifts the 0070 registration rule, adds `claude_code_tools` (default all four, values restricted), restores the required prompt for every agent and gives existing Claude Code agents the built-in documentation prompt; its down migration refuses while a Claude Code agent has a model, any configuration versions (or an active one), a capability switch on (including grounded documents or all collections/code projects), tools other than the default four, more than one prompt version, or server instructions, saved instructions or server connections. Otherwise it drops the other saved selections of Claude Code agents (specific collections, code projects, services and skills, settings rows) and their prompt, keeping only the name and description.

Migration `0073_remove_documentation_generation` removes the one-shot generation (tables, job kind, history and `doc:*` run records), adds `documents.phase_metadata`, and its down migration restores the empty schema only. The Claude Code Test tab no longer links to Generate documents; it says "Claude Code agents can no longer start sessions. Use a native agent."

Migration `0074_native_agent_sessions` adds `agent_sessions_cc.runtime`, `agent_config` and `history`; its down migration refuses while native sessions exist. Native agents' Generate documents sessions run in `agent-session-worker`.

Migration `0075_agent_resources` adds `agents.resources` (`fixed` | `per_run`, default `fixed`); its down migration refuses while per-run agents or per-run configuration versions exist. Migration `0076_run_resources` adds `run_resources` to `agent_sessions_cc`, `agent_sessions` and `operations_runs`; its down migration drops only these records.

Migration `0077_remove_coordinator` removes the multi-agent board and the coordinator: it deletes the board run
records (`board:*`), drops `agent_board_tasks`, `agent_board_members` and `coordinator_settings`, deletes the
coordinator agent with everything it owned, and drops `agents.is_coordinator` with the coordinator-only rules that
0031, 0032, 0072 and 0075 added. Audit events stay. Its down migration restores the empty schema only.

Migration `0078_agent_tools` creates `ka.agent_tools` (`agent_id`, `tool`, `mode` on/off/per_run, `usage`, `subagent_allowed`) and backfills every native agent with the tools its capabilities gave it, so nothing changes at upgrade; `code_graph` stays offered only when a selected code project has a generated graph. Its down migration drops the table and any edited usage text.

- `GET /api/agents` lists profiles with model summaries and `cloned_from`.
- `GET /api/agents/{id}` and `/options` return configuration and available resources.
- `PUT /api/agents/{id}` saves metadata, capability switches and folder/repository/service selections using optimistic revisions. Optional `skill_ids` saves skill assignments in the same transaction as the skills switch; omitting it preserves assignments.
- `PUT /api/agents/{id}` also accepts `runtime` and `claude_code_tools` and `resources`.
- `GET /api/agents` returns `resources` and `accepts` (from the agent).
- `GET /api/agents/{id}/run-resources` returns what a run may be given per kind (`null`: a kind the agent does not use), or `{resources: 'fixed'}`.
- `POST /api/agents/{id}/sessions` accepts `run_resources` with the first message; the session keeps them (`run_resources` on the session).
- `PUT /api/agents/{id}/model` assigns an enabled compatible model and applies the type's model rule.
- `GET /api/agents/{id}/config` returns `runtime` and `claude_code_tools` for both types (`knowledge_admin.knowledge_agents.config.get_agent_config`).
- `PUT /api/agents/{id}/prompt` saves the system prompt; `POST /api/agents/{id}/clone`, `GET|POST /api/agents/{id}/apply` and `GET|POST /api/agents/{id}/reviews/{review_id}[/apply]` are described in [Agent execution](agents.md#clones).
- `/api/agents/{id}/sessions` and `/{session_id}` list and restore conversations;
  `/{session_id}/close` ends one and `/{session_id}/review` saves tuning suggestions.
- `/api/operations/settings`, `/api/operations/configs/{server_id}` and `/api/operations/runs` accept `?agent_id={id}` for any agent. The internal “operations” route/table names remain for compatibility.
- Legacy Search, Code and `/api/mcp/code` routes remain available to existing integrations and honor the corresponding agent scope and capability switches.

Administration endpoints require the existing administrator authorization. Model,
SSH and MCP secrets never appear in profile responses. CRUD uses revision checks;
omitted replacement secrets preserve saved credentials.

`DELETE /api/agents/{id}` accepts `{"revision": 1, "confirmed": true}`. The UI
asks for confirmation. Deletion removes only the agent's settings,
prompt and resource joins. Clones of a deleted agent stay and lose their *Clone of* link. Shared models, skills, servers, repositories and documents
remain. An agent with sessions or run history returns HTTP 409 ("Delete it with its
history, or disable it to keep the history.") unless the body also has
`"with_history": true`.

`GET /api/agents/{id}/history` returns what that would delete:
`{"test_chats", "chats", "claude_code_sessions", "runs"}`. With
`with_history` the same transaction first deletes the agent's run records
(`agent_run_events` with keys `run:<run id>` and `cc:<session id>:<turn>` of those rows only), its runs and their SSH
commands, its chats and their reviews, and its Generate documents sessions (native and Claude Code) and queued inputs. Saved documents are
independent rows and stay. While the agent is working — a documentation session (native or Claude Code) that is
`starting` or `working`, or a `running` operations run — the delete (with or without history) returns HTTP 409
("This agent is working right now. Stop its documentation sessions and runs, or wait for them to finish, then
delete it.") and deletes nothing. The revision is still checked; the `agent.deleted` audit event records the
deleted counts under `history`.
Migration 0025 permits atomic agent deletion and preserves the legacy model
assignment projection when a default agent is removed.

Migration `0023_skills_agents` stores agent-level skill assignments in `ka.skills_agents` with primary key `(agent_id, skill_id)` and foreign keys to agents and the skill library. The same skill can be selected by many agents. Existing agent assignments are migrated from common configuration rows; `ka.agent_config_skills` retains only server-specific overrides. Disabling skills preserves assignments.

Migration `0079_agent_system_prompt` adds `agents.system_prompt` (text, required) filled from each agent's active prompt version, and drops the foreign key that required prompt rows. Its down migration restores the prompt rows: an agent whose prompt was edited after 0079 gets it as a new active version, and text is truncated to 40 000 characters (the old limit).

Migration `0080_clone_agents` makes each agent's active configuration version its configuration (tools, overrides and connections included), then drops `agent_versions`, the prompt tables, the view and guard function, the version column on sessions (`agent_sessions.agent_version_id`) and its constraints; it adds `agents.cloned_from` (nullable, `ON DELETE SET NULL`). Its down migration restores the empty schema only. See `deploy/README.md` for the upgrade notes.

Migration `0081_test_sessions` turns Test chats into sessions of `agent_sessions_cc` (`purpose`), adds `model_calls` and `reasoning_tokens`, moves suggestions onto it and drops the old Test chat tables (their run records stay). See `deploy/README.md`.
