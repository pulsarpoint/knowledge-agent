# Agent profiles

Administration → Agents lists named specialists and one protected coordinator.
The coordinator is marked with `is_coordinator` and cannot be removed or converted
into a specialist. It is permanently enabled and has no enable switch. Its General tab edits name, purpose, model, notes, and execution
limits; Prompts manages its own draft/active versions. Notes join the active system
prompt when running a board task. The board links to this page for settings and
excludes the coordinator from the subagent picker.

Create a specialist with a required name and system prompt, plus optional purpose
and “when to use” description. Every agent owns its configuration; nothing is
inherited from the coordinator. Specialists share these tabs:

| Tab | Configuration |
| --- | --- |
| General | Name, purpose, routing description, availability, Resources (Fixed / Per run), model connection, additional instructions and required active system prompt |
| Skills | Enable skills; select one or more predefined skills from the library |
| Documents | Enable document search; choose folders and whether to include descendants |
| Git paths | Enable read-only Git access; choose repositories |
| SSH | Enable SSH access; choose multiple servers, optional SSH connection overrides, and per-server instructions, skills and MCP selections |
| MCP | Enable MCP tools; choose this agent’s HTTP connections |
| Services | Enable context from selected service descriptions |
| Prompts | View the active system prompt; create, edit, activate and delete inactive versions |
| Versions | Save complete configurations, test drafts independently, activate and roll back |
| Test | Continue a Agents SDK conversation, inspect live activity, close/resume sessions, and request tuning suggestions |

Capabilities are independent of the agent name. Code can enable SSH; SSH can enable Git or document search. Switching a capability off preserves its saved selections. Save the capability switch before editing its dependent SSH or MCP settings. The Skills tab saves its switch and selected skills together. New profiles start with no model and all capabilities off. Existing profiles keep their prior access and active prompts.

**Resources.** A fixed agent always uses its selections. A per-run agent's selections are what a run may be given (All = any); each run passes `code_project_ids`, `collection_ids`, `server_ids` and `service_ids`, validated against them and narrowed into the run's configuration (`knowledge_agents/run_resources.py`); skills and MCP stay fixed. The coordinator is always fixed.

An empty resource selection includes nothing. “All document folders” and “All Git repositories” are explicit options. Services provide descriptive context; for a fixed agent they do not grant access to their servers or repositories. For a per-run agent, a service chosen for a run also brings its code projects and SSH servers into that run when the agent uses code or SSH; each must be within its selections. Skills are administrator-managed instructions. Server-specific instructions and skill/MCP selections are configured within SSH. A server without its own configuration uses the same agent’s instructions and selections. No configuration is inherited from another agent. SSH defaults remain on the shared server inventory. Each agent can override the complete SSH connection for a server; removing that override restores the server defaults. The server’s SSH-enabled flag is always enforced.

Specialist Test tabs run the selected agent directly with its saved configuration.
The first message creates a session and adds `session_id` to the URL. Follow-ups
retain context until the session is closed or inactive for one hour. Reloading
restores history and running turns. New turns keep the session’s pinned configuration; use a new session to test another version.
**Add feedback** beneath a response opens an optional panel and sends the full
configuration, whole stored conversation, selected response and your feedback
to a separate reviewer. Saved suggestions include proposed prompt,
configuration or memory-note changes, with links back to the relevant editor.
Nothing is applied automatically.
The multi-agent board separately runs the Agents SDK coordinator and Agents SDK
specialists. See [execution status](agents.md).

Prompt versions belong to one profile. Creating an agent requires an explicit, nonempty system prompt, which becomes its first active version; later versions start as drafts until activated. Existing published prompts are preserved, including any instructions describing a former specialization; edit these in Prompts when changing the intended purpose. “When to use” records routing guidance. The board coordinator receives this guidance for the explicitly selected team and chooses which specialists to call.

The default IDs `search`, `code`, and `operations` preserve existing integrations.
Every agent has its own configuration; no parent agent is introduced.

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
resources and saves the agent, first active prompt version, settings, and joins
in one PostgreSQL transaction. Duplicate names and unavailable resources return
an error without leaving a partially configured agent. All existing agent
editing, prompt versioning, config preview, and deletion pages work afterward.
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

Migration `0020_agent_profiles` introduced agent identity and resource joins. Migration `0021_agent_capabilities` replaces the fixed `kind` with six independent boolean fields: `documents_enabled`, `repositories_enabled`, `ssh_enabled`, `skills_enabled`, `mcp_enabled`, and `services_enabled`. Existing profiles receive the capabilities their previous runtime supported. Prompts, models, server overrides, encrypted credentials, resource selections and run history remain intact. Migration `0022_required_agent_prompt` adds a deferred foreign key so an agent cannot be persisted without its own prompt record and active version.

Migration `0072_knowledge_agents_config` lifts the 0070 registration rule, adds `claude_code_tools` (default all four, values restricted) and `claude_code_agent_is_not_the_coordinator`, restores the required prompt for every agent and gives existing Claude Code agents the built-in documentation prompt; its down migration refuses while a Claude Code agent is the coordinator, has a model, any configuration versions (or an active one), a capability switch on (including grounded documents or all collections/code projects), tools other than the default four, more than one prompt version, or server instructions, saved instructions or server connections. Otherwise it drops the other saved selections of Claude Code agents (specific collections, code projects, services and skills, settings rows) and their prompt, keeping only the name and description.

Migration `0073_remove_documentation_generation` removes the one-shot generation (tables, job kind, history and `doc:*` run records), adds `documents.phase_metadata`, and its down migration restores the empty schema only. The Claude Code Test tab links to `/documentation/generations/new?agent=<id>`.

Migration `0074_native_agent_sessions` adds `agent_sessions_cc.runtime`, `agent_config` and `history`; its down migration refuses while native sessions exist. Native agents' Generate documents sessions run in `agent-session-worker`.

Migration `0075_agent_resources` adds `agents.resources` (`fixed` | `per_run`, default `fixed`) and `per_run_agent_is_not_the_coordinator`; its down migration refuses while per-run agents or per-run configuration versions exist. Migration `0076_run_resources` adds `run_resources` to `agent_sessions_cc`, `agent_sessions` and `operations_runs`; its down migration drops only these records.

- `GET /api/agents` lists profiles and active model/prompt summaries.
- `GET /api/agents/{id}` and `/options` return configuration and available resources.
- `PUT /api/agents/{id}` saves metadata, capability switches and folder/repository/service selections using optimistic revisions. Optional `skill_ids` saves skill assignments in the same transaction as the skills switch; omitting it preserves assignments.
- `PUT /api/agents/{id}` also accepts `runtime` and `claude_code_tools`, and `resources` (the coordinator: 400 `The coordinator uses fixed resources.`).
- `GET /api/agents` returns `resources` and `accepts` (from the active version).
- `GET /api/agents/{id}/run-resources?version_id=` returns what a run may be given per kind (`null`: a kind the agent does not use), or `{resources: 'fixed'}`.
- `POST /api/agents/{id}/runs` and `POST /api/agents/{id}/sessions` accept `resources` with a new conversation only (follow-ups: 409); sessions and runs return `run_resources`.
- `PUT /api/agents/{id}/model` assigns an enabled compatible model and applies the type's model rule.
- `GET /api/agents/{id}/config` returns `runtime` and `claude_code_tools` for both types (`knowledge_admin.knowledge_agents.config.get_agent_config`).
- Prompt APIs accept the agent ID.
- `/api/agents/{id}/sessions` and `/{session_id}` list and restore conversations;
  `/{session_id}/close` ends one and `/{session_id}/review` saves tuning suggestions.
- `/api/operations/settings`, `/api/operations/configs/{server_id}` and `/api/operations/runs` accept `?agent_id={id}` for any agent. The internal “operations” route/table names remain for compatibility.
- Legacy Search, Code and `/api/mcp/code` routes remain available to existing integrations and honor the corresponding agent scope and capability switches.

Administration endpoints require the existing administrator authorization. Model,
SSH and MCP secrets never appear in profile responses. CRUD uses revision checks;
omitted replacement secrets preserve saved credentials.

`DELETE /api/agents/{id}` accepts `{"revision": 1, "confirmed": true}`. The UI
asks for confirmation. Deletion removes only the agent's settings, prompt versions,
and resource joins. Shared models, skills, servers, repositories and documents
remain. An agent with sessions or run history returns HTTP 409 ("Delete it with its
history, or disable it to keep the history.") unless the body also has
`"with_history": true`.

`GET /api/agents/{id}/history` returns what that would delete:
`{"test_chats", "chats", "claude_code_sessions", "runs", "board_tasks", "board_memberships"}`
(`board_tasks` counts the tasks assigned to the agent and all tasks below them;
`board_memberships` counts the agent's own member rows plus the member rows of those
tasks — exactly the rows the delete removes). With
`with_history` the same transaction first deletes the agent's run records
(`agent_run_events` with keys `run:<run id>`, `cc:<session id>:<turn>` and
`board:<task id>` of those rows only), its runs and their SSH commands, its chats and
their reviews (configuration versions made from a review keep the version and lose the
link), its Generate documents sessions (native and Claude Code) and queued inputs, its board memberships and its board
tasks with their subtasks and member rows. Tasks of other agents above them (such as the
coordinator's task) and beside them stay, and so does the coordinator's own run record
of a task it delegated to this agent (`board:<root task id>`). Saved documents are
independent rows and stay. While the agent is working — a documentation session (native or Claude Code) that is
`starting` or `working`, a `running` operations run, or a `queued` or `running` board
task it owns or is a member of — the delete (with or without history) returns HTTP 409
("This agent is working right now. Stop its documentation sessions, runs and board tasks,
or wait for them to finish, then delete it.") and deletes nothing. The
coordinator is still refused and the revision is still checked; the `agent.deleted`
audit event records the deleted counts under `history`.
Migration 0025 permits atomic agent/prompt deletion and preserves the legacy model
assignment projection when a default agent is removed.

Migration `0023_skills_agents` stores agent-level skill assignments in `ka.skills_agents` with primary key `(agent_id, skill_id)` and foreign keys to agents and the skill library. The same skill can be selected by many agents. Existing agent assignments are migrated from common configuration rows; `ka.agent_config_skills` retains only server-specific overrides. Disabling skills preserves assignments.
