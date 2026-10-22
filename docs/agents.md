# Agent execution status

The agent **Test** tab runs **Test sessions**: sessions in `ka.agent_sessions` with `purpose = 'test'`, run by
`agent-session-worker` exactly like Ask agent sessions (claim and lease, turns, comments delivered
between model calls, Stop with a 30-second grace, closing after 24 hours without activity, the worker's concurrency
limit, no per-turn time limit, no 300-second limit).

- `POST /api/sessions` with `agent_id` and `message` creates a Test session: the agent's current configuration is
  pinned in `agent_config` with credential references only, the title is the first 200 characters of the message, and
  the message is queued as input 1 (201 with the session). A disabled agent and an agent without an enabled model
  (`Select an enabled model in this agent’s General tab.`) are refused. A saved credential that cannot be decrypted
  does not refuse the start: the first turn fails with the worker's message.
- **A run can only narrow the saved agent.** A saved agent stores what it may use as one access object
  (`ka.agents.access`: resource references and the `content_editor` role) and its instructions
  (`ka.agents.instructions`). A run gets the whole access object, or the resources the request picks out of it
  (`resources`, environments and documents included); the run's `content_editor` is the agent's (the request may send
  only `content_editor: false` to switch it off). Every run gets every tool, `run_subagent` and every offered expert;
  the access object decides what a tool may do. The request may choose some of the experts (`expert_ids`). Nothing can
  be added; `tools` and `subagents` are not request fields (sending them is a 400). The model and prompt are the
  agent's own: change them in General, or clone the agent to try another prompt. Refused: "A run cannot make a saved
  agent a content editor.", "A saved agent uses its own model and prompt." and a pick outside the agent's
  access ("<type> <id> is not in the agent's access"); a document picked with `editable: false` is read-only in the
  run. A reference to a deleted or archived resource is skipped; a server without usable SSH stays in the run.
  Event handlers can send to any enabled agent; its run gets the whole access object. An expert narrows its own
  agent's access object to the resources the caller passes.
- `GET /api/sessions/options?agent_id=…` says what may be narrowed (the offered experts and the
  resources its access object holds) and gives the agent's model and prompt as facts. `POST /api/sessions/preview` with the
  same body shows the pinned configuration without starting a run.
- `GET /api/sessions?agent_id=…` lists the agent's Test sessions and its event sessions (with `purpose`), newest first.
- Everything else is the session API: `GET /api/sessions/{id}` (with `purpose`, `model_calls`,
  `reasoning_tokens` and, for Test and (pre-0090) event sessions, `config` — the pinned configuration — and
  `reviews`), `POST …/messages` (Send works while the agent works), `POST …/stop`, `POST …/close`, `GET …/events/history` and `GET …/events`.
  `GET /api/sessions` lists runs (filters `agent_id`, `purpose`, `document_id`, `tag`, `offset`; with `tag` each row
  has proposal counts and open runs come first); every row has its `tags`. `GET /api/sessions/options` and
  `POST /api/sessions/preview` describe and preview a run, and `POST /api/sessions` starts it (Ask agent, a chat on a
  server, service or environment, the Propose wizard and the agent's Test tab use it). `started_from.server_id`,
  `service_id` or `environment_id` starts a resource run (purpose `resource`) tagged `servers/<id>`, `services/<id>`
  or `environments/<id>` and pinned with `started_from` ({type, id, name}); a server run also has `server_id`. A start
  may add its own `tags` (lowercase parts separated by `/`, at most 10 with the resource tag; a resource tag must be
  the one of where it starts); the Propose wizard adds `proposals`. The options take `?server_id=`, `?service_id=` or
  `?environment_id=`: their prompts are the enabled ones tagged `assistant` and with the type (the seeded
  "<Type> assistant" prompt first). The preview returns the pinned `config`, the `first_message` and the same view as
  `GET /api/sessions/{id}/debug`: the run's `instructions` and `tools` as its turns give them to the model, the
  library `prompt` it came from (`prompt_source` `wizard` for a run tagged `proposals`), `agent_instructions`,
  `skills`, `resources` by name and `model`. `GET /api/discovery/proposals?tag=&status=` lists the proposals of the
  runs with a tag, grouped by run, newest run first.
- Document tools: `get_document(document_id, section?, start_line?, end_line?)` reads the run's own draft of a document
  when it has one, and works for the run's documents and, read-only, documents linked (built for or linked) to the
  servers, services, environments and code projects the run was given.
  `edit_document(document_id, old_text, new_text)` and `save_document(document_id, markdown, title?, description?)`
  work only on documents given to the run as editable, and write the run's draft, never the live version. A new document
  is such a resource too: a run started from Documents → New (`resources.documents[{new: {folder, name}, editable: true}]`)
  saves it with `save_document`, which creates the draft of a new document built for the resources the run was started
  for, not for what they bring along (see [documentation](documentation.md)). A run keeps updating its own draft
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
  hostnames, connections and server instructions come from `get_resource`. The edit tools' schemas are the same in
  every run; given another document they answer with the documents the run may change. The proposal tools (`propose_service`, `propose_document`) are offered in every run like
  every tool; their checks allow them only in a content-editor run that may propose for its starting resource, which
  is what the Propose wizard starts (`content_editor: true`): `propose_document` in a run started from a server,
  service or environment, `propose_service` only from a server. A plain chat refuses them. Accepting a document proposal creates a
  draft with the run's `session_id` (a brand-new draft, or a draft of the document it updates; it becomes the run's
  last draft); a person publishes or promotes it. A new document is built for the service it is about, else for the
  run's server, service or environment (for a run without `started_from`, the proposal's server), in that resource's
  folder (`servers/<slug>`, `service-documents/<slug>`, `environments/<slug>`); a document about a service is accepted
  even when the run's resource is gone (then in the service's folder). The new draft is tagged `inventory`
  (the resource's inventory document) when no document tagged `inventory` is built for the resource yet (live or
  brand-new draft); otherwise it is an ordinary document beside it. Discovery never adds the tag to an existing
  document and never changes a document tagged `inventory`: a proposal titled like it is a new document, not a new
  version, and a matched document tagged `inventory` before the accept gets a new document, never a draft of it. A
  person decides whether the new one replaces it (moving the tag) or merges it by hand. `propose_service` proposes a
  service's name, type, role, description, owner and deployment; operating knowledge goes in documents.
- The editing rules for documents are the **Revise a document** prompt in Administration → Prompts, which you can open and change.
- Event handlers (Administration → Events → Handlers, action Send to agent) start an ordinary agent run since
  migration 0103: a session with `purpose = 'event'`, `event_id` and `handler_id` set (unique together, so one run per
  event and handler), the agent's current configuration with its whole access object, everything it has on that it can
  use, the rendered prompt as the first message and the handler's creator as the actor. It is titled
  `<agent> · <event type> <subject>`, shows in the agent's Test tab under Earlier runs, and can be opened and continued.
  There are no automatic retries. When the first turn ends, agent-session-worker writes one notification
  (`ka.notifications.session_id` points at the run) with the answer or the failure; a run closed before its first
  turn gets one failed notification ("Closed."). An event run gives its worker slot back as soon as a turn ends with
  nothing queued; a later message is picked up again as usual. Agent tasks (0090, one-shot calls
  with retries) are removed; `ka.agent_tasks` stays as history, and 0103 failed the ones still waiting.
  Sessions with `purpose = 'event'` created before 0090 stay viewable in the agent's Test tab (**Event** badge).
- Each turn's run record is `cc:<session>:<turn>` (start kind `test_session`, or
  `event_session` for event runs); **Full record** links to it. The `end`
  event of a completed grounded turn carries `grounding` (cited sources and coverage, redacted). 
- `POST /api/sessions/{id}/review` `{ "comment": "...", "turn": n? }` (Test and event runs;
  refused while the agent works) builds the conversation from the session's events — per turn the message, comments sent while
  the agent worked, the tools called (name and summary) and the final answer or error — and runs the tuning reviewer
  (the session's model with every capability off, 240 seconds) with the pinned configuration. The reviewer returns
  advice, `instructions` (a full replacement, or null to keep them) and up to 10 `memory_notes` (`{name, content,
  metadata}`, following the memory module's rules); behaviour rules go to instructions, facts to notes, and the system
  prompt is out of scope. The suggestion is stored in `ka.agent_session_reviews` with `response_turn`
  (`context.changes` = `{instructions}`, `context.memory_notes`); the session detail returns each review's
  `memory_notes`; **Review and apply** reads it.

The Test tab and the run page (`/sessions/:id`) share `RunWorkspace` (`services/admin-ui/app/components`):
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

Every run gets every tool: the whole catalog with its built-in usage text, every tool of the MCP servers in its access
and its experts (see [agent configuration](agent-profiles.md#tools) and `knowledge_admin/tools/README.md`). Built-in
tools are `@tool` functions whose attributes are in `tools/tools.json`, copied into `ka.tools` at start. There is no
per-agent choice and no "needs" rule; each tool's `check_<tool>` refuses what the run's access object does not allow.
`code_graph` answers for a code project of the run that has a current generated graph (`code_graph` results carry its `generated_at`; if
the project was synced after the graph was generated, the answer carries a note to regenerate it or confirm with
`read_file`).

Tool usage per run is recorded as counts: the **Tools** column of Agent runs (top three and `+n`, the tooltip lists
all), the run record, the session page and the Test tab show a line such as `code_graph 12 · read_file 31 · shell_run 4`
or `No tools used`. The workspace counts live: the session detail returns its counts with the id of the
last tool call they include, and the page adds the streamed tool calls after it (a replay after a reconnect counts
once; earlier activity loaded by scrolling up is already counted).

**Subagents (`run_subagent`).** Every native session run (not a subagent's own) gets `run_subagent(task, resources)`. A
subagent works on one task (at most 20,000 characters) without the conversation: its configuration is a copy of the
agent's pinned configuration with every tool but `run_subagent` (the parent's MCP servers kept, no experts, content
editor off), the resource lists narrowed to the given ids (each must be in the agent's current lists; given resources make it a run of exactly those, so
an empty list gives nothing), the built-in prompt `knowledge_agents/prompts/subagent.md`, no instructions, skills off
and the same model. It gets only the turn's already decrypted credentials its configuration references (SSH private
keys only with `ssh_run` or a kept SSH-hosted MCP server) and shares the turn's scratch workspace. A turn starts at
most 16 helpers, subagents and expert calls together ("This turn already started 16 helpers."), and runs
`AGENT_MAX_SUBAGENTS` (default 4) at once, shared with expert calls; later calls wait. The result is the subagent's final answer, redacted, or `Subagent failed: <reason>`; refusals are tool
errors naming the tool or resource. Subagents run only in native agent sessions (Test, Ask agent and new-document runs). Their
events go into the turn's record (`cc:<session>:<turn>`) with `payload.parent_call_id` (the `run_subagent` call id; the
first is `input {task, subagent}`), their model calls into the session's totals and their tool calls into the tool
counts. **Stop** stops the agent and every subagent; a subagent of an ended turn starts no model or tool call.

**Experts (`ask_<slug>`).** A native agent, including the ad-hoc agent of a new-document run, gets one tool per offered expert
(see [agent configuration](agent-profiles.md)): an agent with **Offer as an expert** that is enabled, has a model and
is not the caller (a run's `expert_ids` may narrow them). The name is `ask_` plus the expert's name lowercased with
every run of characters other than `a-z0-9` replaced by `_`, trimmed of `_`, at most 50 characters (an empty slug gives
`expert`; a slug taken by an earlier expert, ordered by name and id, gets `_2`, `_3`, ...). The description is `Ask <name>, an
expert agent. <When to use>` plus ` It uses its own resources; pass some of them to narrow it to those (it accepts:
code projects, document collections, servers, services).` (the kinds in its access object) or, with none of those,
` It uses its own resources.` Parameters: `task` (non-empty, at most 20,000 characters) and, for an expert that accepts
a kind, `resources` (`code_project_ids`, `collection_ids`, `server_ids`, `service_ids`; null: all of its own). The experts a session may call are pinned with its
configuration (`config['experts']`: id, name, tool, When to use, accepts); later changes to the expert list apply to
new sessions, and a new-document run pins every offered expert. The expert's own configuration is loaded when it is
called. Refusals are tool errors: `Expert <name> is not available.` (deleted, no longer an expert, disabled or without
a model), `<kind> <id> is not one of your resources.`, a resource outside the expert's access (`<type> <id> is not in the agent's access`), the 16-helper limit, an empty
or oversized task; `Unknown expert: <tool>.` for a tool the session did not pin. A failure returns `Expert <name>
failed: <reason>` (redacted) and the caller goes on.

A call runs `knowledge_agents/experts.py`: the expert's prompt, instructions, skills, tools and model with its own
access object narrowed to the given resources (a content editor only when its agent and the calling run both are), no conversation, and no expert tools (an expert cannot call experts). It has
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
model of `GET /api/environments/{id}/model`: servers with their deployments, services, connections, external hosts,
relations; every item with its id and revision), `list_servers(environment_id)`, `list_deployments(server_id |
service_id)` and `list_connections(service_id | deployment_id)` (give one; lists stop at 100 with `truncated`). Writes:
`save_server`, `save_service`, `save_deployment`, `save_connection`, `save_relation` (without `id` they create; with `id`
and the `revision` read they update, and omitted fields keep their values) and `remove_inventory_item(kind, id,
revision)`. They call `inventory/store.py`, the one write path the inventory pages use, so the rows are the same; the
audit row carries the session's starting person, the agent and the session. Replies are JSON; a refusal is
`{"refused": text}` and a store error's message is the refusal (a stale revision, a duplicate name, a server still used
by deployments). A server's SSH settings are not part of the tools: a server reply has the model's fields only.
Access (pure `check_<tool>`): an item is usable when it is in the run's access object (children included) or this
session created it (`store.created_in_session`, read from the audit); a deployment when its server, service or
environment is usable; a connection when its from-deployment is; a relation when its from-service is. Reads need the
item usable. Writes need the role `inventory_editor` (`This agent may not change the inventory (inventory editor is
off).`), a session (`Inventory changes need a session.`) and every item they name usable (`This <type> is not available
in this run.`: a deployment names its environment, server and service; a connection its deployments; a relation its
services or code project); a new server or service needs only an environment in the access (`This run has no
environments.`). So an agent can add a server, deploy a service on it and connect it in one run, and cannot touch what
is outside its access unless it created it. The read tools are offered in every run; the write tools only in a session
turn (`catalog.INVENTORY_WRITES`): never to a subagent or an expert, whose role is off anyway.

## Clones

An agent has **one configuration**: what its tabs show is what runs. There are no configuration versions, drafts or
activation. To try a change, work on a clone: clone the agent, change and test the clone, then apply it to the
original, keep it as an alternative, or delete it. A conversation pins the agent's configuration when it starts, so
saving the agent never changes a conversation in progress.

**Clone agent** (the header menu *Actions for <name>* on the agent page, or the **Clone <name>** button in the All
agents list) creates a new agent with an identical configuration: prompt, instructions, model, when to use, "Answer
only from documents", its access object (resources and the content editor role) and tools with their usage text. The
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
