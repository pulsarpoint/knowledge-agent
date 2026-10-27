# Agent execution status

The agent **Chat** tab runs a saved agent's **chat sessions**: sessions in `ka.agent_sessions` with `purpose = 'chat'`
(0142 made the old Test sessions chats; **Run agent** on the Agents page opens the Chat tab), run by
`agent-session-worker` exactly like Ask agent sessions (claim and lease, turns, comments delivered
between model calls, Stop with a 30-second grace, closing after 24 hours without activity, the worker's concurrency
limit, no per-turn time limit, no 300-second limit).

- `POST /api/sessions` with `agent_id` and `message` creates a chat session (the request takes no purpose): the agent's current configuration is
  pinned in `agent_config` with credential references only, the title is the first 200 characters of the message, and
  the message is queued as input 1 (201 with the session). A disabled agent and an agent without an enabled model
  (`Select an enabled model in this agent’s General tab.`) are refused. A saved credential that cannot be decrypted
  does not refuse the start: the first turn fails with the worker's message.
- **A run can only narrow the saved agent.** A saved agent stores what it may use as one access object
  (`ka.agents.access`: resource references; there are no roles, and keys an older stored object still carries are
  ignored) and its instructions (`ka.agents.instructions`). A run gets the whole access object, or the resources the
  request picks out of it (`resources`, environments and documents included). Every run gets every catalog tool but
  the ones switched off ([Tool selection](#tool-selection)), `run_subagent` and every offered expert;
  the access object decides what a tool may do. The request may choose some of the experts (`expert_ids`) and switch
  more catalog tools off (`disabled_tools`). Nothing can be added; `tools` and `subagents` are not request fields
  (sending them is a 400). The model and prompt are the
  agent's own: change them in General, or clone the agent to try another prompt. Refused: "A saved agent uses its own
  model and prompt." and a pick outside the agent's access ("<type> <id> is not in the agent's access"). A reference to a deleted or archived resource is skipped; a server without usable SSH stays in the run.
  Event handlers can send to any enabled agent; its run gets the whole access object. An expert narrows its own
  agent's access object to the resources the caller passes.
- `GET /api/sessions/options?agent_id=…` says what may be narrowed (the offered experts and the
  resources its access object holds) and gives the agent's model and prompt as facts. `POST /api/sessions/preview` with the
  same body shows the pinned configuration without starting a run.
- `GET /api/sessions?agent_id=…` lists the agent's conversations: its chat and event sessions (with `purpose`), newest
  first. `GET /api/agents/{id}/history` counts them as `conversations`.
- Everything else is the session API: `GET /api/sessions/{id}` (with `purpose`, `model_calls`,
  `reasoning_tokens` and, for Test and (pre-0090) event sessions, `config` — the pinned configuration — and
  `reviews`), `POST …/messages` (Send works while the agent works), `POST …/stop`, `POST …/close`, `GET …/events/history` and `GET …/events`.
  `GET /api/sessions` lists runs (filters `agent_id`, `purpose`, `document_id`, `tag`, `offset`; with `tag` open runs come first); every row has its `tags`. `GET /api/sessions/options` and
  `POST /api/sessions/preview` describe and preview a run, and `POST /api/sessions` starts it (Ask agent, a chat on a
  server, service or environment, the Discover sheet and the agent's Chat tab use it). `started_from.server_id`,
  `service_id` or `environment_id` starts a resource run (purpose `resource`) tagged `servers/<id>`, `services/<id>`
  or `environments/<id>` and pinned with `started_from` ({type, id, name}); a server run also has `server_id`. A start
  may add its own `tags` (lowercase parts separated by `/`, at most 10 with the resource tag; a resource tag must be
  the one of where it starts). The options take `?server_id=`, `?service_id=` or
  `?environment_id=`: their prompts are the enabled ones tagged `assistant` and with the type (the seeded
  "<Type> assistant" prompt first). The preview returns the pinned `config`, the `first_message` and the same view as
  `GET /api/sessions/{id}/debug`: the run's `instructions` and `tools` as its turns give them to the model, the
  library `prompt` it came from, `agent_instructions`,
  `skills`, `resources` by name and `model`.
- Document tools: every agent write to a document is a draft in the real documents, which a person publishes on the
  document's page; every write reply ends with "This is a draft: a person publishes it on the document's page.". No
  role: a document is usable (read and drafted) when it is in the run's access or linked (built for or linked) to a
  server, service, environment or code project in it. `list_documents(resource)` lists a server's, service's or
  environment's documents and its children's (the inventory document first) with `published` and `your_draft`, plus
  the new documents the run drafted for it. `get_document(document_id, section?, start_line?, end_line?)` reads the
  run's own draft of a document when it has one. `new_document(resource, title, content, description?, tags?)` creates
  a new document as a draft built for a server, service or environment in the run's access (`resource`: `{type, id}`
  or `{type, name}`), named by the first free slug in the project from the resource's name and the title (`-2`, `-3`,
  ... when taken); a title already used by a document linked to the resource is refused with its id ("A
  document titled "<title>" is already linked to this <type> (<id>); update it with update_document instead."). It
  returns the draft's id: only that run uses it until a person publishes the draft, and it stays the document's id
  afterwards. `update_document(document_id, content, title?, description?, resource?)` saves the run's draft with the
  whole text; `resource` adds a link that the draft holds and publishing adds to the document (migration 0130 lets a
  draft of a live document hold `linked` links). `edit_document(document_id, old_text, new_text)` replaces one exact
  passage. `get_document`, `update_document` and `edit_document` also take the id of one of the run's own drafts (a
  person mentions a draft in the chat as `[Draft "<title>" · <id>]`): a draft of a live document stands for that
  document (its run's draft of it), a new document's draft id is the document's id; another run's or a person's draft
  id is refused like any other document. The write tools are offered only in a session turn: never to a subagent or an
  expert. Documents → New → Write with an agent is a chat that creates documents with `new_document` for its resources
  (see [documentation](documentation.md)). A run keeps updating its own draft
  (`agent_sessions.last_draft_id`). Promote and discard delete drafts, and promoting one draft of a document deletes the
  others; the run's next save then starts a new draft of the live version, and the tool result says what happened to
  the earlier one: promoted, discarded, or removed because another draft was promoted. A person's edit makes a run's
  draft theirs: of an existing document, the next save starts a new draft; of a new document, the save is refused
  until they promote or discard it. Every
  per-resource tool checks its argument against the run's pinned allowlist ("This document is not available in this
  run.").
- The inventory tool `get_resource(resource_type, resource_id)` (`server`, `service`, `environment` or `code_project`,
  and its uuid) is offered in every run, like every tool, and follows the same allowlist: it answers only about the resources the run was given, with everything recorded
  about it and links to related resources; a linked resource the run was not given, anything else, or a malformed id,
  gets "The <type> <id> is not available in this run." — the same answer as for something that does not exist.
  Its answer has the resource's `inventory_document` (`{id, title}` of its live inventory document — the live document
  tagged `inventory` built for it, the most recently updated when several — or null; a code project's is always null) and `documents`: every live document with an active link to the resource or to one of its
  direct children (an environment's services and servers, a server's services, a service's code projects and the
  servers it is deployed on), as `{id, title, tags, description_short, connection, resource: {type, id,
  name}}` — `description_short` is the description's first line (160 characters at most, "…" when cut), `resource` is
  the resource it is listed under (the resource itself when linked there, else the first child). The resource's
  inventory document first, then its other documents, then the children's grouped by child, by title within a group.
  Only documents `get_document` would read in this run are listed (linked to a resource the run was given, or given
  directly).
  `get_document` reads documents the run was given or linked to those resources; a document linked only to resources
  the run was not given is not available. The prompt names a run's resource
  in one line (`Resource: server <uuid> (<name>)`) and lists servers and services only by id, name and description;
  hostnames, connections and server instructions come from `get_resource`. The document tools' schemas are the same in
  every run; given another document they answer with the documents the run may use. Proposals are gone (migration 0131): a discovery run (the
  Discover sheet, the seeded **Server discovery** prompt) saves inventory findings (services, deployments, connections
  and relations) as drafts with the `save_*` tools (`save_inventory_draft` for many at once), which a person confirms or
  discards in the environment workspace, and drafts documents with `new_document` (built for the resource it is for)
  and `update_document` / `edit_document` (an existing document); a person publishes them on their pages. There is no
  approval step and no `ka.discovery_proposals`.
  Operating knowledge goes in documents.
- The editing rules for documents are the **Revise a document** prompt in Administration → Prompts, which you can open and change.
- Event handlers (Administration → Events → Handlers, action Send to agent) start an ordinary agent run since
  migration 0103: a session with `purpose = 'event'`, `event_id` and `handler_id` set (unique together, so one run per
  event and handler), the agent's current configuration with its whole access object, everything it has on that it can
  use, the rendered prompt as the first message and the handler's creator as the actor. Since 0138 the run also records
  `schedule_id` (the schedule at its event chain's root; neither id is a foreign key); with the handler's
  `include_previous_output`, the last valid output of the same handler and schedule (`schedule_id IS NOT DISTINCT
  FROM`) is appended to the first message as a `# Previous run` block (never the system prompt), and the pinned
  configuration records `previous_output_from`. It is titled
  `<agent> · <event type> <subject>`, shows in the agent's Chat tab under Earlier runs, and can be opened and continued.
  There are no automatic retries. When the first turn ends, agent-session-worker writes one notification
  (`ka.notifications.session_id` points at the run) with the answer or the failure; a run closed before its first
  turn gets one failed notification ("Closed."). An event run gives its worker slot back as soon as a turn ends with
  nothing queued; a later message is picked up again as usual. Agent tasks (0090, one-shot calls
  with retries) are removed; `ka.agent_tasks` stays as history, and 0103 failed the ones still waiting.
  Sessions with `purpose = 'event'` created before 0090 stay viewable in the agent's Chat tab (**Event** badge).
- Each turn's run record is `cc:<session>:<turn>` (start kind `chat_session`, or
  `event_session` for event runs; 0142 renamed the stored `test_session` kinds); **Full record** links to it. Its `instructions` event keeps the instructions sent
  to the model, with the Skills section when the run has skills.
- `POST /api/sessions/{id}/review` `{ "comment": "...", "turn": n? }` (Test and event runs;
  refused while the agent works) builds the conversation from the session's events — per turn the message, comments sent while
  the agent worked, the tools called (name and summary) and the final answer or error — and runs the tuning reviewer
  (the session's model with every capability off, 240 seconds) with the pinned configuration. The reviewer returns
  advice, `instructions` (a full replacement, or null to keep them) and up to 10 `memory_notes` (`{name, content,
  metadata}`, following the memory module's rules); behaviour rules go to instructions, facts to notes, and the system
  prompt is out of scope. The suggestion is stored in `ka.agent_session_reviews` with `response_turn`
  (`context.changes` = `{instructions}`, `context.memory_notes`); the session detail returns each review's
  `memory_notes`; **Review and apply** reads it.

The Chat tab and the run page (`/sessions/:id`) share `RunWorkspace` (`services/admin-ui/app/components`):
a header (agent, type, resources, status, tokens, tools used), turns with a collapsed **Worked** block and the answer,
a sidebar **Agents in this run** (a drawer on narrow screens) with the page's sections, and a message
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

### Finding documents

Every agent finds documents with `search_documents(query, limit?)`: full-text search over the published documents the
run may read (see [Document tools](documentation.md#document-tools)), then reads one with `get_document`.
Migration 0071 removed the former Reader application's conversations and its
`ka.reader_settings` and two columns of the old Test chat table.

Saving the agent's settings changes what runs next; conversations already started keep the configuration they began
with.

The former code-agent service and its "Ask the code" escalation were
removed; agents read code with their own tools (`list_files`, `read_file`,
`code_search`, `ast_grep`, `code_graph`).

`knowledge_admin.agents.agent()` runs a configured specialist with the Agents SDK.
The caller provides a saved config, task, history, and decrypted credentials. Responses and Chat Completions
connections use the corresponding SDK model classes; native Anthropic/Bedrock uses
`LitellmModel`. See [the runtime documentation](../services/admin-api/README.md).

Git synchronization, Jira import, document search and connection tests
are functional integrations. Connection-test buttons may contact their configured
server or model. Keycloak authentication and Phoenix infrastructure remain.

See [agent configuration](agent-profiles.md), [SSH settings](server-operations.md),
[LLM connections](llm-settings.md), and [PostgreSQL migrations](migrations.md).


## Tools

Every run gets every tool it has not switched off: the catalog with its built-in usage text, every tool of the MCP
servers in its access and its experts (see [agent configuration](agent-profiles.md#tools) and
`knowledge_admin/tools/README.md`). Built-in tools are `@tool` functions whose attributes are in `tools/tools.json`,
copied into `ka.tools` at start. There is no "needs" rule; each tool's `check_<tool>` refuses what the run's access
object does not allow.

### Tool selection

Catalog tools can be switched off for a saved agent and, further, for a run; an off tool is never offered, so the model
cannot call it (and the adapter's backstop refuses a forged call: `Tool <name> is not available in this run.`).

- **A saved agent:** `ka.agents.disabled_tools` (migration 0135), read and written by the agent profile API
  (`GET`/`PUT /api/agents/{id}`, `disabled_tools`; left out of a `PUT`, the list is kept). The off tools are stored,
  so a tool added to the catalog later is on for every agent. A clone copies the list.
- **A run:** the start request's `disabled_tools`. A saved agent's run switches off its agent's tools and the request's
  (a request cannot switch one back on); an ad-hoc run the request's. The pinned configuration records the set
  (`config['disabled_tools']`), and the session detail returns it (`disabled_tools`), so **Start new chat with these
  settings** keeps it. A saved agent's start options carry its own (`agent.disabled_tools`), shown locked.
- **Subagents and experts** inherit the calling run's off tools; an expert adds its own agent's. They may withhold
  more (as today), never fewer.
- `GET /api/tools` lists the catalog tools for the Tools list: `{tools: [{name, group, description}]}`, ordered by group
  then name (admin only). The UI drops saved off names that are not in it before starting a run.
- Names are checked against the catalog: an unknown name in a request is refused, `Unknown tool: <name>.` (400). A
  stored name of a tool later removed from the catalog is dropped wherever the list is read (the profile, a run's
  start, an expert's load, clone and Apply), so it never refuses the next settings save.
- `agents/runner.instructions_and_tools` is the one place that withholds them, so the offered tools, the backstop
  (`context['tools']`), the Debug tab and the start preview all follow it. The rules are pure functions in
  `tools/selection.py` (`disabled_for`, `validate`, `known`).
- Scope: the catalog tools. MCP servers and experts keep their own per-run selection.
`code_graph` answers for a code project of the run that has a current generated graph (`code_graph` results carry its `generated_at`; if
the project was synced after the graph was generated, the answer carries a note to regenerate it or confirm with
`read_file`).

Tool usage per run is recorded as counts: the **Tools** column of Agent runs (top three and `+n`, the tooltip lists
all), the run record, the session page and the Chat tab show a line such as `code_graph 12 · read_file 31 · shell_run 4`
or `No tools used`. The workspace counts live: the session detail returns its counts with the id of the
last tool call they include, and the page adds the streamed tool calls after it (a replay after a reconnect counts
once; earlier activity loaded by scrolling up is already counted).

**Subagents (`run_subagent`).** Every native session run (not a subagent's own) gets `run_subagent(task, resources)`. A
subagent works on one task (at most 20,000 characters) without the conversation: its configuration is a copy of the
agent's pinned configuration with every tool but `run_subagent` (the parent's MCP servers kept, no experts, content
editor off), the resource lists narrowed to the given ids (each must be in the agent's current lists; given resources make it a run of exactly those, so
an empty list gives nothing), the built-in prompt `knowledge_agents/prompts/subagent.md`, no instructions, the parent
run's skills (`config['skills']`) and the same model. It gets only the turn's already decrypted credentials its
configuration references (SSH private
keys only with `ssh_run` or a kept SSH-hosted MCP server) and shares the turn's scratch workspace. A turn starts at
most 16 helpers, subagents and expert calls together ("This turn already started 16 helpers."), and runs
`AGENT_MAX_SUBAGENTS` (default 4) at once, shared with expert calls; later calls wait. The result is the subagent's final answer, redacted, or `Subagent failed: <reason>`; refusals are tool
errors naming the tool or resource. Subagents run only in native agent sessions (Test, Ask agent and Write with an agent runs). Their
events go into the turn's record (`cc:<session>:<turn>`) with `payload.parent_call_id` (the `run_subagent` call id; the
first is `input {task, subagent}`), their model calls into the session's totals and their tool calls into the tool
counts. **Stop** stops the agent and every subagent; a subagent of an ended turn starts no model or tool call.

**Experts (`ask_<slug>`).** A native agent, including the ad-hoc agent of a Write with an agent run, gets one tool per offered expert
(see [agent configuration](agent-profiles.md)): an agent with **Offer as an expert** that is enabled, has a model and
is not the caller (a run's `expert_ids` may narrow them). The name is `ask_` plus the expert's name lowercased with
every run of characters other than `a-z0-9` replaced by `_`, trimmed of `_`, at most 50 characters (an empty slug gives
`expert`; a slug taken by an earlier expert, ordered by name and id, gets `_2`, `_3`, ...). The description is `Ask <name>, an
expert agent. <When to use>` plus ` It uses its own resources; pass some of them to narrow it to those (it accepts:
code projects, servers, services).` (the kinds in its access object) or, with none of those,
` It uses its own resources.` Parameters: `task` (non-empty, at most 20,000 characters) and, for an expert that accepts
a kind, `resources` (`code_project_ids`, `server_ids`, `service_ids`; null: all of its own). The experts a session may call are pinned with its
configuration (`config['experts']`: id, name, tool, When to use, accepts); later changes to the expert list apply to
new sessions, and a Write with an agent run pins every offered expert. The expert's own configuration is loaded when it is
called. Refusals are tool errors: `Expert <name> is not available.` (deleted, no longer an expert, disabled or without
a model), `<kind> <id> is not one of your resources.`, a resource outside the expert's access (`<type> <id> is not in the agent's access`), the 16-helper limit, an empty
or oversized task; `Unknown expert: <tool>.` for a tool the session did not pin. A failure returns `Expert <name>
failed: <reason>` (redacted) and the caller goes on.

A call runs `knowledge_agents/experts.py`: the expert's prompt, instructions, tools and model, the calling run's skills
(`experts.inherit`: no new read), with its own
access object narrowed to the given resources (no document or inventory write tools), no conversation, and no expert tools (an expert cannot call experts). It has
`run_subagent` like every run: its subagents run in slots of their own (`AGENT_MAX_SUBAGENTS`), count towards the turn's
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

**Wake-ups (`schedule_wakeup`, `list_wakeups`, `cancel_wakeup`).** A run with a session (`config['session_id']`) can
continue its own session later, for example to look again at a slow deployment or a ticket waiting for a reply.
Subagents, experts and internal model calls do not get these tools. `schedule_wakeup(message, in_minutes | at)` takes
exactly one of `in_minutes` (1 … 43,200) or `at` (an ISO time, 1 minute to 30 days ahead; without an offset it is UTC)
and a `message` of 1 … 2,000 characters, and returns `{id, due_at}`. `list_wakeups()` returns the session's pending
wake-ups (`{id, due_at, message}`, by due time) and `cancel_wakeup(id)` cancels one of them. Refusals (exact): `A
session can have at most 3 pending wake-ups.`, `Choose a time between 1 minute and 30 days from now.`, `Give either
in_minutes or at.`, `Wake-ups need a session.`, `This wake-up is not pending in this session.` (another session's,
delivered or already cancelled). A wake-up is a queued input of the session (`kind 'wakeup'`, `not_before` its due
time, the session's starting person as its actor; `capabilities/wakeups.py`). The session worker claims a session for
it only once it is due, and delivers it only between turns (never as a mid-turn comment): the turn's input reads
`Scheduled wake-up (set <ISO time>): <message>`, with the session's history, pinned access and model, like a person's
next message. A wake-up fires once; the woken turn may schedule the next one. An event-run session continues too, but
its Jira reply or hand-back (first turn only) is not repeated. Idle close (24 hours) skips a session with a pending
wake-up; **Stop** keeps its wake-ups and **Close** discards them. The session page lists the pending wake-ups above the
message box with **Cancel** (`DELETE /api/sessions/{id}/wakeups/{wakeup_id}`, the same rule as `cancel_wakeup`; audited
as `generation.session.wakeup_cancelled`); the session detail and its live state carry them as `wakeups: [{id,
due_at, message, created_at}]`, and the detail's `inputs` and `queued` count people's messages only.

**Inventory tools (`capabilities/inventory_tools.py`).** Reads: `get_environment_model(environment_id)` (the environment
model of `GET /api/environments/{id}/model`: member servers (also those with no deployment) with their deployments,
services, connections, external services, external hosts, relations; every item with its id, revision and status),
`list_servers(environment_id)` (the member servers; each with its `environment_ids`), `list_deployments(server_id |
service_id)` and `list_connections(service_id | deployment_id)` (give one; lists stop at 100 with `truncated`);
`get_resource` carries the status too (of the resource, and of the deployment, membership, relation or connection behind
each link). Every entry read has `status`: `draft` (an agent's, waiting for a person) or `confirmed`, so an agent sees
drafts (its own and others') and does not repeat them.

**Every agent write is a draft.** Writes: `save_environment`, `save_server`, `save_service`, `save_deployment`,
`save_connection`, `save_relation` (without `id` they create a draft; with `id` and the `revision` read they update a
draft, which stays a draft, and omitted fields keep their values), `remove_inventory_item(kind, id, revision)` (a draft
environment, server, service, deployment, connection, relation or `code_dependency`) and `save_inventory_draft`
(below). `save_relation(kind="code_depends_on", from_code_project_id, to_code_project_id, description)` drafts a code
project dependency (`ka.code_project_dependencies`, 0143: a repository that imports a library repository, recorded when
the agent reads package.json, go.mod, pyproject.toml …); both code projects must be in the run's access (a run never
creates a code project); a code project never depends on itself ("A code project cannot depend on itself.") and a pair
is recorded once ("This code project already depends on <name>."). An update gives `kind="code_depends_on"` with the
`id`; `get_resource` on a code project lists its `depends_on` and `used_by` code projects, each link with
`dependency_id` and `revision`. An existing
confirmed entry is refused: `This <type> is confirmed; only a person can change it. Add a new draft instead, or describe
the change in your answer.` Only a person confirms or discards a draft (the environment workspace; `POST
/api/inventory/{kind}/{id}/confirm` and `/discard`). `save_server(environment_id)` puts the server (new or existing,
confirmed too) in that environment as a draft membership; the environment must be in the run's access or created by the
session (a membership is never removed by an agent); a draft deployment adds a draft membership when its server is not
a member yet. `save_service(address)` sets where an external service is reached; `save_connection(to_service_id)`
connects to an external service, which must be in the access or created by the session (a custom service gives the
store's refusal). They call `inventory/store.py` with `status='draft'`, the one write path the inventory pages use; the
audit row carries the session's starting person, the agent and the session. Replies are JSON; a refusal is
`{"refused": text}` and a store error's message is the refusal (a stale revision, a duplicate name, a server still used
by deployments). A server's SSH settings are not part of the tools: a server reply has the model's fields only.

**`save_inventory_draft`** drafts a whole picture in one call; entries refer to each other by name, every part is
optional:

```json
{
  "environment": "prod",
  "servers": [{"name": "app-1", "hostname": "10.0.0.5", "description": "Docker host"}],
  "services": [
    {"name": "clickhouse", "service_type": "builtin", "service_role": "database", "description": "Analytics store"},
    {"name": "clickhouse-backup", "service_type": "builtin", "service_role": "worker", "part_of": ["clickhouse"]},
    {"name": "backblaze-b2", "service_type": "external", "address": "s3.us-east-005.backblazeb2.com"}
  ],
  "deployments": [
    {"service": "clickhouse", "server": "app-1", "runtime": "docker compose", "runtime_name": "clickhouse",
     "port": 9000, "version": "clickhouse/clickhouse-server:24.8"},
    {"service": "clickhouse-backup", "server": "app-1", "runtime": "docker compose",
     "runtime_name": "clickhouse-backup", "version": "altinity/clickhouse-backup:2.7.4"}
  ],
  "connections": [
    {"from": "clickhouse-backup", "from_server": "app-1", "to": "clickhouse", "to_server": "app-1",
     "port": 9000, "protocol": "tcp"},
    {"from": "clickhouse-backup", "to": "backblaze-b2", "port": 443, "protocol": "https"},
    {"from": "clickhouse", "to_host": "smtp.example.com", "port": 25, "protocol": "smtp"}
  ],
  "relations": [{"from": "clickhouse-backup", "kind": "depends_on", "to": "clickhouse"}]
}
```

Names are matched without case: an entry in the same call first, then a recorded entry (confirmed or draft) the run
may name (in its access or created by the session). A recorded confirmed entry named here is used as an end, never
changed (the fields given for it are ignored, but its `part_of` still adds draft relations); a recorded draft is
updated with the fields given; anything else is created as a draft. `environment` names an existing environment or
drafts a new one; every server listed and every server a deployment runs on joins it (a draft membership, listed in the
reply), and deployments and connections are in it. A deployment is the recorded one of that service on that server in
the environment: with `runtime_name` the one with that runtime name, without it the only one there (several: refused,
`deployments[0]: service clickhouse has several deployments on server app-1 (clickhouse, ch2); give runtime_name.`);
else it is created. A connection is matched by its ends, port and protocol, a relation by its ends and kind; so the same
call twice (with or without the runtime names) creates nothing new, and the same relation or connection twice in one
call is refused (`listed twice`). A connection's `to` is a service: an external one is the target itself
(`to_service_id`), any other is reached through its deployment in the environment; `from_server` / `to_server` are
needed only when a service runs on more than one server, `from_runtime_name` / `to_runtime_name` only when it runs more
than once on that server, and every ambiguity names the field that resolves it. `part_of` on a service adds `part_of`
relations; a `built_from` relation names a code project in the access. The
pure `resolve_draft(payload, recorded)` turns the payload into a plan (steps create / update / use, a membership add)
and refuses the first entry it cannot resolve with `<part>[<index>]: <reason>` (for example `connections[0]: no
service named clickhous. Known services: clickhouse, clickhouse-backup.`, `connections[0]: clickhouse runs on app-1,
app-2; give from_server.`); `apply_draft` writes the plan through the store in one transaction, so on any error
(a store error is named the same way) nothing is written. The reply lists `created`, `updated` and `used`, each `{kind,
name, id}`, and says the entries are drafts a person confirms in the environment workspace.

**Tool documentation.** Every inventory tool's description says what it is for, that writes are drafts a person
confirms, every field with its format and allowed values (service types, roles, runtimes as free text, ports 1–65535,
protocol lowercase such as `tcp`/`https`/`postgres`, hostnames without scheme or port), how entries are named, and an
example; the field descriptions in the schemas say the same per field.

**Access** (pure `check_<tool>`): an item is usable when it is in the run's access object (children included) or this
session created it (`store.created_in_session`, read from the audit); a deployment when its server, service or
environment is usable — for a write (update, remove, or a connection from it) all three, as for a new deployment, so a
service held through one environment does not open its deployments elsewhere; a connection when its from-deployment is;
a relation when its from-service is. Reads need the item usable. Writes need no role: a session (`Inventory changes need
a session.`) and every item they name usable (`This <type> is not available in this run.`: a deployment names its
environment, server and service; a connection its deployments; a relation its services or code project); a new
environment, server or service needs only an inventory resource (an environment, server or service) in the access
(`This run has no environments or servers or services.`; in `save_inventory_draft` prefixed with `<part>[<index>]: `). So an agent can draft a server, deploy a
service on it and connect it in one run, and cannot touch what is outside its access unless it created it. The read
tools are offered in every run; the write tools only in a session turn (`catalog.INVENTORY_WRITES`): never to a subagent
or an expert. An older stored access object with a role that no longer exists still loads (an unknown role is
ignored).

## Skills

A **skill** is a folder in the Agent Skills format: a `SKILL.md` (a YAML frontmatter with `name` and `description`, then
the instructions) and any files it points to (`references/`, `scripts/`, `assets/`, binary files included). The code is
in `skills/`: `rules.py` (pure: frontmatter, paths, limits, changed files, which folders cleanup deletes), `store.py`
(folders on disk), `imports.py` (zip and git) and `api.py` (`/api/skills`).

**Storage.** The files are on disk, the database holds the metadata. `ka.agent_skills` keeps `name` and `description`
(read from `SKILL.md` at every import or save: `name` 1-64 lowercase letters, digits and single hyphens; `description`
1-1024 characters), `enabled`, `revision`, `source` (`{kind: "upload", file}`, `{kind: "git", url, ref, path, commit}`
or `{kind: "editor"}`) and `folder`, the current files relative to the data folder. Folders live under
`SKILLS_FOLDER` (default `skills`, writable only in `admin-api`, `SKILLS_ROOT` in the container) as
`skills/<skill id>/<version>/`, `<version>` being the short git commit (12 characters) or a UTC timestamp
`YYYYMMDDTHHMMSSZ` for uploads and editor saves; `<version>.json` beside each folder records when it was written and
its source. Executable bits are kept.

**Updates write a new folder, then switch.** An import into a skill, an **Update from source**, an upload or an editor
save writes the complete new folder under a staging name, renames it into place, then sets `folder` in one update (and
`revision` +1). A run that already started keeps the folder it began with. Each skill keeps its last 3 folders and the
current one (for **Restore**, which switches `folder` back to a kept folder with that folder's source); older ones are
deleted only when no running run pinned them: a session that is not closed whose config's `skills` name the folder
(`store.pinned_folders`). Cleanup runs after every update of that skill and over every skill folder at
admin-api startup, which also removes deleted skills' folders that were pinned when they were deleted.

**Limits.** A folder holds at most 500 files, 5 MB per file and 10 MB in all. Paths are relative, without `..`, empty or
`.` segments or backslashes; names that differ only in case, a file that is also a folder of other files, symbolic
links and special files are refused. A zip's declared sizes are checked per skill, and against 50 MB for the whole
archive, before any entry is read; entries are then read with a running count, so an understated size cannot get past
the limits. A damaged, encrypted or unsupported archive is refused. A refused import writes nothing, and an update
that fails after writing its new folder deletes that folder.

**Import.**

- *Zip* (`POST /api/skills/import/zip?file=<name>`, the archive as the body, at most 25 MB): `SKILL.md` at the
  archive's root, or in its only top-level folder, is one skill; otherwise every top-level folder holding a `SKILL.md`
  (in the only top-level folder, too) is a skill, so one archive can import several. `?preview=true` lists them (name,
  description, files) without writing anything. `__MACOSX/` and `.DS_Store` entries are ignored.
- *Git* (`POST /api/skills/import/git` with `url`, `ref` and `path`, `preview` as for a zip): the admin-api fetches the
  ref (the default branch when empty) with depth 1 into a temporary folder, with the code projects' Git settings (no
  hooks, no prompts, HTTPS only, 2-minute timeout), reads the folder at `path` (the repository root when empty, without
  `.git`) and records the commit. A public repository needs no credential; when a code project has the same remote URL
  and a saved token, that token is used.
- A name that exists is refused: `A skill named <name> exists; update it instead.` Importing into the skill itself
  (`POST /api/skills/{id}/update/upload` with one skill in the zip, or `POST /api/skills/{id}/update/source`, which
  re-imports a git skill from its URL, ref and path; the clone runs before the row is locked) writes a new folder and returns the changed files (`added`,
  `removed`, `modified`).

**Editor.** `POST /api/skills` (`name`, `description`, optional `instructions`) creates a skill from a `SKILL.md`
template. `GET /api/skills/{id}` returns the skill with its files (path, size, executable) and its kept folders
(`versions`: version, when, source, current); `GET /api/skills/{id}/files/<path>` returns one file (`text` is null for a
binary file). `PUT /api/skills/{id}/files` (`revision`, `changes`: write a file from `text` or `content_base64`, with
`executable`; `delete` a file or folder; `rename_to` a file or folder) saves a new folder from the current one plus the
changes, after the same checks as an import. Every change of a skill takes its `revision` (a stale one is refused
with 409). `POST /api/skills/{id}/restore` (`version`), `PATCH
/api/skills/{id}/status` and `DELETE /api/skills/{id}` (the row, and its folders a running run has not pinned) complete
the API. Each change is audited (`skill.created`, `skill.updated`, `skill.saved`, `skill.restored`, `skill.deleted`,
`skill.enabled`, `skill.disabled`).

**At run time.** Every run gets every enabled skill: run start (`experts.pin`, for every path that pins a
configuration) reads the project's enabled skills once and pins them as `config['skills']` = `[{id, name, description,
folder}]` in name order (`skills.run_skills`; a skill whose folder is missing on disk is left out). Skills are not part of
the access model (no `skill` access type; an older stored skill entry is ignored, and migration 0141 removed them). The
pinned `folder` (relative to the data folder) means an update, a restore or a switch-off during the run does not change
what it reads or runs, and a skill enabled after the start is not in that run. The run's instructions list the skills by
name, description and the absolute path of its `SKILL.md` (`<DATA_ROOT_DIR>/<folder>/SKILL.md`, e.g.
`/work/data/skills/<id>/<version>/SKILL.md`; no list when `read_file` is switched off for the run); the run reads it with
`read_file`. There is no skill tool: the skill folders are roots of `list_files`, `read_file` and `search_files` (which need a code
project or a skill) and `shell_run` mounts them read-only at the same paths (scripts run with no network; outputs go to
the scratch working directory). The path is the same everywhere because every container mounts the data folder at
`/work/data` (the admin-api's writable `skills/` bind sits at the same path). A pinned folder that is missing is left
out of the roots and the shell mounts (and is not listed); the run's other folders and skills keep working. Debug lists
the run's skills with their folder and the skills used (`skills.skills_used`: the `SKILL.md` files read with
`read_file`); the session detail has the same `skills_used`. An agent that stored `load_skill` as an off tool keeps
working: the removed name is dropped when it is read (`selection.known`).

**Running `test_shell_worker.py` in the container.** The sandbox tests skip where there is no bwrap (macOS). They need
the shell-worker image (rebuilt by `make up`) with bwrap, `/work/agent-workspaces` and its seccomp setting; the image has
no pytest, so run from the repository root:

```bash
docker compose --env-file .env -f deploy/compose.yaml run --rm --no-deps -T \
  -v "$PWD/services/admin-api/tests:/services/admin-api/tests:ro" \
  -e UV_CACHE_DIR=/tmp/uv -e PYTHONDONTWRITEBYTECODE=1 \
  --entrypoint /bin/sh shell-worker -c \
  'cd /services/admin-api && uv run --no-sync --with pytest pytest -q -p no:cacheprovider tests/test_shell_worker.py'
```

`run` keeps the service's user, read-only root, dropped capabilities, seccomp and `agent-workspaces` volume. All tests
should pass, including `test_a_skill_script_runs_at_its_listed_path_and_cannot_write_there`. If uv cannot write its
cache, add `--tmpfs /tmp/uv`.

## Output schemas

An **output schema** is a named JSON Schema a run can be asked to answer with. Schemas live in a library (**Agents** ->
**Output schemas** in the sidebar); an agent carries none: the schema is chosen per run. Every save of a changed schema adds a version
(`current_version`); old versions are kept, because runs and consumers depend on the exact shape. Saving only a new name or
description changes the revision, not the version.

A schema must be in the strict subset structured output accepts, checked on save by the pure `check_schema`
(`output_schemas/rules.py`), which lists every problem with its location (`$.properties.tags: 'minItems' is not
supported`):

- the root is an object; every object lists all its properties in `required` and sets `additionalProperties: false`;
- keywords: `type` (`object`, `array`, `string`, `number`, `integer`, `boolean`, `null`, or a list of them for a
  nullable value), `enum`, `description`, `properties`, `required`, `additionalProperties`, `items` (arrays need `items`);
- anything else is refused, including `$ref` and `$defs`: a schema is one self-contained tree. A schema over 64 KB (bytes of
  its UTF-8 JSON) is refused.

`validate(schema, value)` returns the ways a value does not match (`$.steps[0].title: 1 is not of type 'string'`).

- `GET /api/output-schemas` (with `used_by`: the handlers that use each), `POST /api/output-schemas`,
  `GET|PUT /api/output-schemas/{id}` (GET includes the JSON and the version list; PUT takes `revision`: 409 `This output
  schema changed since you opened it.`), `GET /api/output-schemas/{id}/versions/{version}`.
- `DELETE /api/output-schemas/{id}?revision=` is refused while a handler uses the schema: 409 `Used by: <handler names>.`
- `POST /api/output-schemas/check` takes `{schema, sample?}` and returns `{problems, sample_errors}`; `sample_errors`
  is `null` without a sample (or when the schema has problems) and `[]` when the sample matches.

### Runs with an output schema

- A saved agent's run takes `output_schema_id` (and `output_schema_version`; none: the current one) on
  `POST /api/sessions`: the Chat tab (`chat`) and event handlers (`event`, from
  `event_handlers.output_schema_id` / `output_schema_version`; a handler without a version follows the current one). The
  ad-hoc runs are chats on a resource or a document (`resource`, `documents`, `revision`): 400 `Chats do not take an
  output schema.` The run pins `config.output_schema = {id, name, version, schema, follows_current}` (and
  `agent_sessions.output_schema_id` / `output_schema_version`), so later edits do not change it; the Debug tab shows it.
  `follows_current` is true when no version was named: **Start new chat with these settings** then picks **Current**
  again rather than the version this run pinned.
- The runner gives the model `JsonSchemaOutput` (`agents/runner.py`) as the output type: strict when the model's
  `capabilities.supports_structured_output` is on (LLM settings; on for OpenAI-style models by default); otherwise
  non-strict, and the instructions end with `Finish with JSON only, matching this JSON Schema: <schema>`.
- The session worker validates every final answer against the pinned version (`output_schemas/runs.verdict`). A mismatch
  is answered once more in the same turn (`Your final answer does not match the output schema: <errors>. Answer again
  with JSON only.`, each error capped at 300 characters); still invalid, the turn fails with `Output did not match the
  schema`, `agent_sessions.output_error = {errors, raw}` (and `output` cleared) and an `error` run event
  `{type: OutputInvalid, message, errors, raw}`. An answer over 256 KB fails at once (`Output is larger than 256 KB.`).
  An answer wrapped in one code fence (```` ```json ````) is read as the JSON inside it.
- A valid answer is stored in `agent_sessions.output` and emits the event `run.output` (source `run`, source id the
  run) with `{run_id, agent_id, agent_name, schema_id, schema_name, schema_version, output, started_from, handler_id,
  event_id}` (`started_from` is null for the saved-agent runs that take a schema; `handler_id`/`event_id` say
  where the run came from). A valid output clears `output_error`.
- Loop guard: one counter, the event chain's `depth` (at most 5, `MAX_DEPTH`). A run a handler starts from an event
  pins it (`config.event = {id, root_id, depth}`), and its `run.output` joins that chain one level deeper
  (`parent_id` the event, its `root_id`, `depth + 1`); a run started any other way records `run.output` as a root
  (depth 0). Emitted events and handler-started runs climb the same depth, so every loop stops: an Emit handler on an
  event at depth 5 fails with `Chain too deep (5 levels).`, and a Send to agent handler with an output schema on an event
  at depth 5 is recorded `skipped` with `Stopped: the event chain is 5 levels deep; its run.output would be deeper.` (the
  event is still processed, not failed). A handler without a schema still starts its agent: that run emits no
  `run.output`.

### Reading the output

- `POST /api/sessions?wait=<1-120>` starts the run and holds the answer until it ends (polling the session row every 0.5 s,
  without blocking the server): `200` with `{id, status, output}`, or for a failed run `{id, status: "failed", output:
  null, error, errors?, raw?}`; when the time is up, `202` with `{id, status: "running"}`. Without `wait` the start
  answers `201` at once, as before.
- `GET /api/sessions/{id}/status` returns `{id, status, started_at, last_activity_at, progress: {turns, tool_calls,
  step}, output_ready, error?}`. `status` maps the session's: starting is `queued`, working is `running`, waiting is
  `finished` for a run with an output schema (otherwise `waiting`), failed and stopped are `failed` (stopped: `Stopped.`),
  closed is `finished` (`failed` for a run with a schema that closed without an output). A run with a schema whose turn
  is done reports `finished` even when a wake-up or a queued message may later add a turn and replace its output.
  `step` is the tool running now (the current turn's last tool call without a result) or `thinking` while a turn runs,
  else null. `tool_calls` also counts the expert (subagent) calls made inside the run's turns. `output_ready` is true
  only when the status is `finished`, or `failed` with an output kept from an earlier turn (so `/output` answers 200).
- `GET /api/sessions/{id}/output` returns `{schema: {id, name, version}, output, valid, error?, errors?, raw?}` (a
  failed run: `output` null, `valid` false, `error` the failure reason as in `/status`, and `errors` and `raw` when its
  answer did not match). 404 `This run has no output schema.` without a schema; 409 `The
  run has not finished.` while it runs.
- `GET /api/agent-runs?has_output=true|false` lists only runs that have (or lack) an output; `false` also lists runs
  that are not sessions (expert calls aside), which never have one.
- `GET /api/agent-runs?schedule_id=<uuid>` lists only the runs whose event chain that schedule started (expert calls
  aside). `GET /api/sessions/{id}` carries `started_by` (`{schedule_id, schedule_name, handler_id, handler_name}` for a
  handler run, names null when deleted; null otherwise) and `previous_output` (`{session_id, finished_at}` of the run
  whose output its task included, or null).

## Clones

An agent has **one configuration**: what its tabs show is what runs. There are no configuration versions, drafts or
activation. To try a change, work on a clone: clone the agent, change and test the clone, then apply it to the
original, keep it as an alternative, or delete it. A conversation pins the agent's configuration when it starts, so
saving the agent never changes a conversation in progress.

**Clone agent** (the header menu *Actions for <name>* on the agent page, or the **Clone <name>** button in the All
agents list) creates a new agent with an identical configuration: prompt, instructions, model, when to use,
its access object (its resources) and tools with their usage text. The
name is `<name> (copy)`, then `(copy 2)` and so on while taken. Sessions, runs, run records, reviews and audit history
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

Saving the agent locks it for the save, so it is serialized with Apply and with other saves.

**Add feedback → Suggest improvements** stays. **Review and apply** on a suggestion opens *Apply suggestions*: the
advice; **Instructions** (the current text, the proposed replacement to edit, and **Apply instructions**, ticked when a
change is proposed); and **Memory notes**, each with a checkbox (ticked by default), editable name and content, and
**Already exists — open in Memory** (unticked) when an entry has that name. With instructions ticked, **Apply to this
agent** (new conversations use it) or **Apply to a new clone** (clone first, then write them there); with only notes,
**Save memory notes**. When notes were sent, the sheet then lists the saved notes (links to `/memory?entry=<id>`) and the
refused names with their message; **Done** closes it. A review never changes the system prompt.

- `GET /api/agents/{id}/reviews/{review_id}`: the suggestion, `changes.instructions`, the agent's current
  `instructions`, `revision` and `fingerprint`, and `memory_notes` — each with `exists`, `memory_id` and
  `current_content` when an entry already has that name (404 `Suggestion not found for this agent.`).
- `POST /api/agents/{id}/reviews/{review_id}/apply`: `{target: 'agent' | 'clone', revision, fingerprint, instructions:
  string | null, memory_notes: [{name, content, metadata}]}` → `{agent_id, saved: [{id, name}], refused: [{name,
  message}]}`. Notes are saved with `memory.store.create` (embedded like the memory API), author = the person and the
  reviewed session, metadata plus `source: review`; a taken name is refused (`An entry named <name> already exists.`)
  while the other notes and the instructions still apply. The revision/fingerprint check (409 `The agent changed since
  you opened the suggestion. Review it again.`) applies only when instructions are applied; 400 `Nothing to apply.` when
  both are empty. Audit `agent.suggestion.applied` records `instructions_changed`, `memory_saved` and
  `memory_refused`.

Snapshots of running conversations store credential references, never private keys or provider tokens. Credentials
are resolved at execution time; disabled agents/models/SSH/MCP resources still block use. File contents and external
server state are live; a removed credential or moved folder can make an old conversation unavailable.

Migrations `0079_agent_system_prompt` and `0080_clone_agents` removed agent versions; see
[PostgreSQL migrations](migrations.md) and the upgrade notes in `deploy/README.md`.
