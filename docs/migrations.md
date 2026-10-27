# PostgreSQL migrations

The deployment uses the official `golang-migrate` CLI v4.19.1. Migration files
use `<version>_<name>.up.sql` and, when rollback is supported,
`<version>_<name>.down.sql`. The current version and dirty flag are stored in
`public.golang_schema_migrations`. The former checksum ledger,
`public.schema_migrations`, is retained on upgraded databases as adoption
evidence.

Fresh databases need no baseline step. `make db-migrate` applies all pending up
migrations, and the Compose application services wait for that command to
succeed. `make db-test` exercises a fresh install, a no-change rerun, legacy
adoption, record retention, and failed/dirty migration behavior in disposable
PostgreSQL databases.

## One-time adoption of an existing database

Do this before starting a checkout that uses golang-migrate. Do not let the
normal migration service run against the existing database first, because it
would try to replay migration 1.

1. Back up PostgreSQL and retain the current `.env`, especially the PostgreSQL
   password and credential encryption key.
2. Keep application services stopped while adopting, and verify that PostgreSQL
   contains `public.schema_migrations` from the legacy runner.
3. Build only the migration image with `make db-migrate-image`.
4. Run `make db-adopt` once. It verifies that every legacy version is a
   contiguous prefix of this checkout and that every stored SHA-256 checksum
   matches the original up SQL. It then records that last verified version in
   `golang_schema_migrations` without replaying schema SQL.
5. Run `make db-migrate` to apply migrations added after the verified baseline.
   A second `make db-migrate` should report no change.

`db-adopt` refuses an empty legacy ledger, a gap, an unknown migration, a
checksum mismatch, or any existing golang-migrate ledger. It never deletes or
updates `public.schema_migrations`. Investigate a refusal against a restored
copy rather than forcing the live database forward.

## Failure and rollback

New migrations that can be reversed include a real down file. Historical
migrations have no invented down files, so a request to migrate down through
them fails instead of reporting a rollback that did nothing. Put transaction
boundaries in migrations that need atomic multi-statement behavior.

When an up migration fails, golang-migrate records its version as dirty. Inspect
the migration error and database state before changing the ledger. After fixing
or manually reverting the failed migration, an operator can run the migration
container with `force <last-good-version>` and then `up`. Never use `force` to
bypass the legacy adoption checks.

The migration file format and CLI behavior follow the
[official migration format](https://github.com/golang-migrate/migrate/blob/master/MIGRATIONS.md),
[CLI documentation](https://github.com/golang-migrate/migrate/blob/master/cmd/migrate/README.md),
and [PostgreSQL driver options](https://github.com/golang-migrate/migrate/blob/master/database/postgres/README.md).

## 0082_ad_hoc_sessions

`ka.agent_sessions_cc.agent_id` becomes nullable: a Generate documents session runs an ad-hoc native agent pinned in
`agent_config` and has no `ka.agents` row. A check allows a null `agent_id` only for a `documents` native session with a
pinned config. The down migration deletes such sessions (their inputs cascade; run records stay) and makes `agent_id` required again.

## 0078_agent_tools

Creates `ka.agent_tools` (one row per agent and tool: `mode` on/off/per_run, optional edited `usage`,
`subagent_allowed`). It backfills every native (`runtime = 'openai'`) agent with the tools its capabilities gave
it before tools were chosen: file tools and `shell_run` for Code, or Documents without "Answer only from documents";
`code_search`, `ast_grep` and `code_graph` for Code; `search_documents` and `read_document` for Documents;
`report_not_covered` for grounded Documents; `ssh_run` for SSH. Other tools are not inserted (off); MCP tools
without a row are on. `code_graph` is still offered only when a selected code project has a generated graph. The down
migration drops the table, which removes edited usage text; afterwards tools follow the capabilities again.

## 0079_agent_system_prompt

Adds `ka.agents.system_prompt` (text, required), filled from each agent's active prompt version, and drops the foreign
key that required prompt rows. The down migration restores the prompt rows: an agent whose prompt was edited after 0079
becomes a new active version, an agent without prompt rows gets its prompt as version 1, and text is truncated to
40 000 characters.

## 0080_clone_agents

Each agent with an active configuration version gets that version's configuration (name and description when the name
is free, tools, overrides and connections included; the agent type and enabled stay). Resources are referenced live:
the version's frozen copies of resource details are not kept. One audit event `agent.version.materialized` is written
per migrated agent, holding the version. Agents in an installation without exactly one application project are skipped
(they keep their own configuration). It then drops `agent_versions`, the prompt tables, the view, the guard function,
the version column on sessions (`agent_sessions.agent_version_id`) and its constraints, and adds `agents.cloned_from` (nullable FK,
`ON DELETE SET NULL`). The down migration restores the empty schema only; versions and prompt history do not come back.

## 0081_test_sessions

Test chats become sessions run by agent-session-worker. `ka.agent_sessions_cc` (renamed `ka.agent_sessions` by 0084) gets `purpose` (`documents` for Generate
documents sessions, `test` for the agent Test tab), `model_calls` and `reasoning_tokens` (native sessions add them per
model call); `document_name` and `folder` become nullable, with checks that a documentation session has both and a Test
session has neither (and no draft, draft turn or document), and that a Test session is native. `ka.agent_session_reviews`
is recreated on `agent_sessions_cc(id)` (`ON DELETE CASCADE`) with `response_turn`. The old Test chats are dropped:
`ka.agent_sessions`, `ka.operations_runs`, `ka.operations_commands` and the old suggestions; their run records
(`run:<id>` in `ka.agent_run_events`) stay readable in Agent runs. The down migration deletes Test sessions (their run
records stay) and restores the old tables empty; the old Test chats do not come back.

## 0083_session_notices

Live session pages stop polling. Triggers send `pg_notify('ka_sessions', …)`, delivered on commit: a statement-level
`AFTER INSERT` on `ka.agent_run_events` sends one notice per session among the inserted `cc:<session uuid>:<turn>`
rows, `{"s": "<session uuid>", "e": <newest id>}` (other run keys send nothing); `AFTER UPDATE OR DELETE` on
`ka.agent_sessions_cc` and `AFTER INSERT OR UPDATE OR DELETE` on `ka.agent_session_inputs` send `{"s": "<session
uuid>"}` per row (identical notices in one transaction are folded). The down migration drops the triggers and their
functions; without them no session page hears of changes. The polling code is gone from this version, so rolling back
0083 requires the previous app version too.

## 0084_remove_claude_code

Claude Code agents are gone. Each Claude Code agent becomes a native agent with the tools its enabled capabilities give
(`ka.agent_tools` rows, as a switch to native did). Claude Code sessions are deleted with their inputs and reviews;
saved documents stay, and their run records (`cc:<session>:<turn>` in `ka.agent_run_events`) stay readable in Agent
runs. It drops `ka.agents.runtime` and `claude_code_tools`, drops `ka.agent_sessions_cc.runtime`, `claude_session_id`
and `cost_usd` with their checks, makes `agent_config` required, and renames the table to `ka.agent_sessions` with its
constraints and indexes (`agent_sessions_cc_*` becomes `agent_sessions_*`); the 0083 notice triggers stay on the renamed table. Old audit rows keep their
`claude_code.session.*` actions. The down migration restores the columns and the old table name (every agent and session
comes back native); the deleted Claude Code sessions and the original agent types do not come back.

## 0085_agent_experts

Agent experts. Adds `ka.agents.expert` (default false: **Offer as an expert**). A caller's choice for one expert is a
`ka.agent_tools` row `expert:<agent id>`; only `mode = 'off'` is stored (no row = on) and `subagent_allowed` is always
false, so the check `subagents_do_not_spawn` now covers `tool LIKE 'expert:%'` (replacing the unused `ask_agent:%`).
Deleting an agent deletes the `expert:<its id>` rows of every caller. A partial index `agent_run_events_expert_calls`
serves the expert-calls listing of Agent runs. The down migration drops the column and the index, deletes every
`expert:%` row (callers lose their choices of experts to switch off, and the expert switch is gone) and restores the
old check.

## 0086_schedules

Schedules. `ka.schedules` holds one row per scheduled target (`target_kind` `jira.pull` with `target_id` = the Jira
connection): a five-field cron expression, an IANA time zone, `enabled`, `next_run_at` (NULL while paused unless Run now
is pending, `run_requested`), the lease of a run in progress and the last result. `UNIQUE (project_id, target_kind,
target_id)`. `ka.schedule_runs` keeps each schedule's newest 100 runs (`trigger` `schedule` or `manual`) and is deleted
with its schedule. repository-worker runs due schedules. The down migration drops both tables: auto pull settings and
run history are lost; the Jira tickets already pulled stay.

## 0087_schedule_params

`ka.schedules.params` (jsonb object, default `{}`) holds target settings. For `jira.pull` it is `{jql, limit}`: the
automatic pull's own query, separate from the manual Fetch tickets query; without it the pull uses the connection's last
fetch query and limit. The down migration drops the column (pulls fall back to the last fetch query).

## 0088_events

`ka.events` records things that happened at a source, as a queue for later handling (no status yet). The scheduled
Jira pull records `jira.ticket.created` for a ticket it has not recorded before and `jira.ticket.updated` (with
`changes`) when Jira's `updated` moved; `dedupe_key` (`jira:<connection>:<issue>:<updated>`, unique per project) keeps
repeated pulls from recording twice. The partial index `events_jira_issues` serves the pull's lookup of each ticket's
latest event. The down migration drops the table and every recorded event.

## 0089_event_handlers

`ka.events` gains a status (`pending`, `processed`, `failed`, with `processed_at`) and a chain: `parent_id`, `root_id`
(a trigger sets it to the event's own id for a root), `depth` (0–5) and `handler_id`. Source `handler` marks events a
handler emitted. Existing events are marked `processed` so new handlers do not replay them. `ka.event_handlers` holds
handlers (type pattern, conditions, action `emit` with Jinja templates or `agent` with an agent and a prompt template;
`agent_id` has no foreign key, so deleting an agent keeps the handler and its runs fail with `Agent not found.`) and
`ka.event_handler_runs` what each handler did with each event. `ka.agent_sessions.purpose` allows `event` (sessions
a handler started). The down migration deletes handler-emitted events and event sessions, then drops the tables and
columns.

## 0090_agent_tasks_notifications

`ka.agent_tasks` holds the agent calls Send-to-agent handlers queue (id `agent-task:<event>:<handler>`, so processing
an event again queues nothing new): agent, rendered prompt, status `queued`/`running`/`succeeded`/`failed`, attempts,
`claims` (times picked up, never decreased; it names the run record `task:<id>:<claims>`), the next attempt's time and
the running worker's lease. `ka.notifications` holds what they produced (kind `agent.response`, status, title, body),
one list for every user. `ka.event_handler_runs.agent_task_id` links a run to its task. Deleting an event keeps its
tasks and notifications (their `event_id` becomes NULL). The down migration drops both tables, every task and
notification, and the column.

## 0091_manual_events

`ka.events.source` also allows `manual`: events made by hand from Events → New event, to test handlers. A hand-made
event has source `manual` or, when simulating Jira, `jira`; either way `source_id` is NULL and `dedupe_key` is
`manual:<uuid>` (that prefix marks it, and no pulled ticket is ever compared with it). The other checks stay: only a
handler's event has a parent, a handler and a depth. The down migration deletes every `manual` event and the events handlers
made from them (their handler runs go with them; agent tasks and notifications keep their rows with `event_id` NULL) and restores the old check;
hand-made `jira` events are kept, as they look like pulled ones.

## 0092_system_graph

The system graph (Environments → System map). `ka.kg_nodes` holds nodes in four layers (`business`, `software`,
`data`, `infrastructure`); each node has at most one parent, in the same project and layer (`parent_id` NULL = top
level), sibling names are unique ignoring case, and a trigger refuses a node inside itself or a branch deeper than 12
levels. `ka.kg_edges` holds typed, directed edges between any two nodes of the project (same or different layers); an
edge between a node and a node that contains it is refused when made. `ka.kg_node_documents` links nodes to
`ka.documents`. Deleting a node with children is refused; deleting a node removes its edges and document links;
deleting a document removes its links. The down migration drops the three tables and both trigger functions.

## 0093_graph_inventory_sync

System graph nodes can link to inventory rows (`kg_nodes.server_id`, `service_id`, `deployment_id`, `environment_id`,
`repository_id`; at most one per node, one node per row, the layer must match). The links are foreign keys checked at
commit: the admin API's sync (`graph_sync.py`), run by every inventory write and at start-up, creates, renames, moves
and releases linked nodes and keeps edges marked `synced` (`depends_on`, `uses`, `instance_of`, and the new type
`includes`). A delete that skipped the sync fails at commit. `kg_graph_version` holds one number that every statement on
`kg_nodes`, `kg_edges` and `kg_node_documents` increases (with a `ka_graph` notification), so processes know when to
rebuild their in-memory graph; editing a document that a node has attached increases it too (editing an unattached
document does not). The down migration deletes synced and `includes` edges, deletes linked nodes with nothing
attached, and drops the links, the version table and its trigger.

## 0094_graph_environment_tree

Environments contain their servers in the system graph: a server has one linked node per environment it is in (both
`server_id` and `environment_id` set) and a top-level node when it is in none (`environment_id` NULL). The unique rule
becomes one node per (server, environment) pair; an environment's own node is the only node with `environment_id` and no
`server_id`. The down migration turns the per-environment copies into hand-made nodes and restores one node per server.

## 0095_inventory_links

Connections between inventory entries replace the stored system graph. `inventory_links` holds `depends_on`
(service → service), `built_from` (service → code project) and `connects_to` (any entry → any entry) with a description
and string attributes; each end is a foreign key to its entry table and deleting an entry deletes its links.
`inventory_documents` attaches documents to entries. Rows of `service_dependencies` and `service_repositories` move into
`inventory_links`; documents attached to graph nodes move to the entry behind the node (a deployment's to its service),
and attachments of hand-drawn nodes are dropped (the documents stay). `kg_nodes`, `kg_edges`, `kg_node_documents`,
`kg_graph_version` and their triggers are dropped. `service_assets` reads the new table and gains `links` (the service's
`connects_to` links). The down migration refuses while `connects_to` links, link attributes, `built_from` descriptions or attachments exist;
otherwise it restores the old tables and an empty graph, which the old code's start-up sync refills.

## 0096_prompt_tags

Prompts get `tags` (lowercase letters, digits and `-`, up to 40 characters, at most 10, no duplicates), backfilled as
`documentation` plus the old `input_kind`. `input_kind` is retired but kept with `DEFAULT 'mixed'`, because the
per-workspace seed functions still insert it; a BEFORE INSERT trigger tags an untagged insert the same way, so seeded
prompts of new workspaces are tagged too. The down migration drops the tags, the trigger and the default. Rolling 0096 back drops the tags, and prompts created after the upgrade keep only `input_kind = 'mixed'`.

## 0097_server_discovery

Server discovery runs are agent sessions with `purpose = 'discovery'` and the new `server_id` (set exactly for that
purpose; deleting the server deletes its runs). `ka.discovery_proposals` holds what a run proposes — services and
documents — until an administrator accepts or rejects them. Every workspace gets the prompt "Server discovery", tagged
`server-discovery` and `ssh`. The down migration refuses while discovery runs exist and when the seeded prompt was
changed; otherwise it removes the prompt, the table and the column. A document version may now be `generated` without a
(retired) generation run, so discovery documents can say where they came from; the down migration refuses while such a
version exists and restores the old rule.

## 0098_document_drafts

`ka.document_drafts` holds unpublished versions of a document (`document_id`, `base_version_id`) or of a new document
(`slug`, `folder`, `resource_ids` to link on promote): written by a person (`manual`) or a Generate documents run
(`agent`, `session_id`), promoted to a live version (`promoted_version_id`) or discarded by a person. A decided draft is
frozen by a trigger. `ka.document_sources` records what a draft or live version was built from (the document it
revises and the documents its author read, with a title/version snapshot); `ka.document_links` holds the
`depends_on` links people add. Nothing in `documents`, `document_versions` or `document_details` changes. The down
migration refuses while open drafts, sources or links exist.

## 0099_document_agent

`ka.agent_sessions` gets `purpose = 'revision'` (an Ask agent run on a document, ad hoc like discovery) and
`target_document_id`, set exactly for those runs; deleting the document deletes its runs (and, as before, its drafts). The down migration refuses while revision runs exist.

## 0100_sessions_api

Seeds the prompt **Revise a document** (tag `document-revision`) in every workspace and for new ones: the Ask agent's
editing rules, now ordinary prompt text. Open Ask agent runs (pinned with the old document tools) are closed with "Closed
by an update; start a new chat."; their queued messages are discarded. `target_document_id` now only records where a run
was started. Prompts tagged `document-revision` that people made before this update no longer get the editing rules and document outline appended by code; copy the rules from "Revise a document" into them. The down migration refuses while the seeded prompt is customized and removes it; closed runs stay closed.

## 0101_write_documents

Seeds the prompt **Write a document** (tags `documentation` and `new-document`) in every workspace and for new ones: how an
agent writes a new document (read what it was given, write with `save_document`, change a passage with `edit_document`,
never say it is published) is now ordinary prompt text. Open Generate documents runs (they were told to answer with the
whole document) are closed with "Closed by an update; start a new chat."; their queued messages are discarded. Custom
prompts tagged `documentation` that people made before this update should tell the agent to write with `save_document`;
copy the rules from "Write a document". The down migration refuses while the seeded prompt is customized and removes it;
closed runs stay closed.

## 0102_discovery_runs

A server discovery run is now an ordinary run started from a server (`POST /api/sessions` with
`started_from.server_id`); the code no longer adds its own system prompt or a final-report rule, so what the agent does
is the prompt's text. The seeded prompt **Server discovery** gets a version 2 with that text (read what the inventory
records first, explore with read-only commands with `ssh_run` on the server named in the context, never copy secrets,
propose with `propose_service` and `propose_document`, correct a refused proposal and call again, finish with a short
report; 0129 replaces `propose_service` with `save_inventory_draft`) and version 2 becomes active, but only when nobody changed the prompt (one version, revision 1). New workspaces
get the new text as version 1. A customized "Server discovery" prompt, or another prompt tagged `server-discovery`, is
left alone: add that the server is the one named in the context, to run commands with `ssh_run` on it, and the rule
"When a proposal is refused, the result says why: correct it and call again." Open discovery runs (pinned with the old
prompt) are closed with "Closed by an update; start a new chat."; their queued messages are discarded and their
proposals stay reviewable. `POST /api/servers/{id}/discovery` and `GET /api/servers/{id}/discovery` are removed (use
`GET /api/sessions?server_id=`). The down migration re-activates version 1 and removes version 2 when version 2 is
still active and untouched, and restores 0097's seed for new workspaces; closed runs stay closed. It only reverts prompts
0102 itself rewrote (workspaces created after 0102 keep the new text).

## 0103_event_runs

A "Send to agent" event handler now starts an ordinary agent run (purpose `event`) instead of queueing an agent task.
`ka.agent_sessions` gains `event_id` and `handler_id` (both `ON DELETE SET NULL`), unique together, so processing an
event again starts no second run; `ka.notifications` gains `session_id`, the run a notification reports on (written when
the run's first turn ends). Agent tasks still queued or running are no longer run: they are failed with "Not run: event
handlers start agent runs now. Send the event again." and each gets a failed notification; send their events again to
start runs. `ka.agent_tasks` stays as read-only history (the Events and Notifications pages still show old tasks). The
down migration refuses while runs started by an event exist (delete them first) and drops the new columns; the failed
tasks stay failed.

## 0104_document_model

Live documents and drafts share `ka.documents`; earlier live contents move to `ka.document_archive`; links move to
`ka.document_resources`, one row per link with provenance and history (see [documentation](documentation.md)).

- Each document row gets its current version's content and number (`version`, title, …, `origin`, `from_draft`,
  `version_created_at`/`version_created_by`); every other version becomes an archive row with its id and number.
- Every **open** draft becomes a draft row: a draft of a document gets `draft_of` and `base_version`; a draft of a new
  document becomes a brand-new draft with its slug and folder, and its `resource_ids` become `built_for` link rows
  (ids that no longer exist are dropped). When a live document now has the brand-new draft's folder and slug, the draft
  becomes a draft of that document instead. Promoted and discarded drafts are not carried over.
- Rows of the five link tables become `linked` links with `added_how = 'migrated'`.
- `ka.document_sources` rows move to the new owners (a draft row, a live row or an archive row); `source_version_id`
  is replaced by `source_document_id` + `source_version`.
- `ka.agent_sessions.last_draft_id` is filled with each run's latest draft; the run's draft status after the draft is
  gone comes from the audit log.
- `ka.document_versions`, `ka.document_drafts` and the five link tables are dropped; `ka.document_details` is
  recreated over live rows and `ka.live_documents` and `ka.document_drafts_view` are added. Triggers refuse document
  links, inventory attachments, discovery proposals and a run's document that point at a draft.

The down migration refuses with "Document link provenance or history exists and would be lost; …" once any live
document has a link that is not an active migrated link (a link built for it, added or removed by a person, a run or
discovery). Otherwise it rebuilds `document_versions` from the archive and live rows, `document_drafts` from the draft
rows (as open drafts), the five link tables and the previous views, and drops the new tables and columns. Drafts
promoted or discarded after 0104 are deleted rows, so they do not come back.

## 0105_discovery_draft

Accepting a discovery proposal for a document creates a draft instead of writing the live document (see
[documentation](documentation.md)). `ka.discovery_proposals` gains `draft_id` (`REFERENCES documents(id) ON DELETE SET
NULL`, indexed), the draft an accepted document proposal made; it may name a draft (the live-document trigger covers
`document_id` only). Publishing a brand-new draft attaches it to the inventory entry of the accepted proposals with
that `draft_id`. Proposals accepted before 0105 keep `draft_id` NULL (their documents were written live). The down
migration drops the column; drafts made by accepting proposals stay as drafts.

## 0106_resource_runs

A chat on a server, a service or an environment is a run with purpose `resource`, tagged with that resource.
`ka.agent_sessions` gains `tags` (`text[]`, default empty, GIN-indexed): at most 10 distinct labels of lowercase
letters, digits, `_` and `-` in parts separated by `/`, at most 120 characters each (`servers/<id>`, `services/<id>`,
`environments/<id>`, and `proposals` for a Propose wizard run). Purpose `discovery` becomes `resource`; `server_id` may
be set only for a resource run (the server it started from) and is null for one started from a service or an
environment. Every discovery run becomes a resource run tagged `servers/<server_id>`: its pinned `started_from_server`
becomes `started_from` (`{type: 'server', id, name}`) and the proposal tools it had on are kept as `proposal_tools`;
a discovery run from before `started_from_server` (0097–0101, closed by 0102) gets its `started_from` from `server_id`
and the server's name (marked `started_from_backfilled`), so its document proposals can still be accepted.
`ka.discovery_proposals.server_id` becomes nullable (proposals of service and environment runs). Prompts may have the
input kind `environment`, and every workspace (existing ones, and new ones by a trigger on `ka.projects`) gets three
seeded, editable prompts tagged `assistant` and their type: **Server assistant**, **Service assistant** and
**Environment assistant**, the default prompt of a new chat on that type. `get_resource` replaces
`get_server_info_basic`, `get_server_services`, `get_server_docs`, `get_service_info`, `get_service_docs` and
`get_project_info`; it is not chosen per agent (every run with resources has it), so the old tools' `ka.agent_tools`
rows are deleted. The seeded **Server discovery** prompt gets a new version that reads with `get_resource`, but only
when nobody changed it (its active version is the newest and holds 0102's text); new workspaces get that text as
version 1.

The down migration refuses while runs started from a service or an environment, or proposals without a server, exist
(delete them first), and while an assistant prompt has been customized (more than its one version or a changed
revision); otherwise it deletes the assistant prompts and their trigger. A prompt you created with input kind
`environment` must be changed or deleted first. Resource runs become discovery runs again with
`started_from_server` (backfilled runs lose their `started_from`), and their tags are dropped. The Server discovery
prompt's `get_resource` version is removed when it is still active and untouched, and new workspaces get 0102's text
again. The old inventory tools' rows are not restored, so those tools are off for every agent.

## 0107_inventory_document

One inventory document per server, service or environment. `ka.documents` gains `inventory` (boolean, default false;
never set on a draft of a live document). A constraint trigger on `documents` and `document_resources`, checked at
commit, refuses a flagged document without exactly one active `built_for` link to a server, service or environment
(`An inventory document must be built for one server, service or environment.`) and a second flagged document (live
or brand-new draft) built for the same resource (`<Type> <name> already has an inventory document.`); the check takes
an advisory lock per resource, so two transactions cannot both add one. Deleting a resource (its built-for link cascades away) clears its inventory document's flag; the document stays. `document_details`, `live_documents` and
`document_drafts_view` gain `inventory` (a draft of a live document reports its document's). `inventory_documents` and
its trigger are dropped without carrying attachments over (they were test data); an entry's documents are read from
`document_resources`.

The down migration drops the flag (inventory documents become ordinary documents; their links stay), restores the
views and recreates `inventory_documents` empty, as 0095 defines it with 0104's live-document trigger.

## 0108_tools

`ka.tools` holds one row per built-in tool with its attributes (`name`, `position`, `tool_group`, `label`, `summary`,
`needs`), the table copy of `services/admin-api/src/knowledge_admin/tools/tools.json`. The migration only creates the
table; the admin-api start replaces its rows with the file (`tools/fixture.py` `sync_tools`), so the file is the only
writer. `agent_tools.tool` is not a foreign key, because it also names MCP tools (`mcp:<server>:<tool>`). The down
migration drops the table; nothing else depends on it, and the catalog reads the file, not the table.

## 0109_tags_not_flags

Tags instead of special columns. `ka.documents.inventory` is replaced by the tag `inventory`: a flagged document and
its drafts get `inventory` appended to `tags` (never twice; `updated_at` stays), then the column, its check
constraint, the commit-time constraint triggers (one per resource), the trigger clearing the flag when a resource's
built-for link is deleted and their functions are dropped, and `document_details`, `live_documents` and
`document_drafts_view` are recreated without it. The inventory document of a resource is now the live document tagged
`inventory` built for it (the most recently updated when several), else a brand-new draft so tagged; nothing enforces
one per resource. `ka.services.operations_notes` is dropped (test data: the notes are not converted) and
`service_assets` is recreated without it.

The down migration refuses (`… would be lost; …`) when two documents tagged `inventory` (live or brand-new drafts) are
built for one server, service or environment. Otherwise it re-adds `operations_notes` empty and `service_assets` with
it, re-adds `inventory` with 0107's constraint, triggers and views, and sets it on the live documents and brand-new
drafts tagged `inventory` with exactly one built-for link, to a server, service or environment (the tags stay).

## 0110_jira_replies

`event_handlers.reply_to_jira` (boolean, default false; only a Send to agent handler may set it): post the agent's
answer as a comment on the Jira ticket that triggered the run. `notifications` gains the comment's delivery state:
`jira_status` (`pending`, `posted`, `failed` or null for no comment), the connection and issue id it posts to, the
issue key for the link, the posted comment's id and creation time, attempts, the next attempt time (set exactly while
pending) and the last error. Repository-worker posts pending rows (`events/jira_replies.py`); the Jira pull reads
posted rows to skip an update caused only by its own comment. The down migration drops the columns, their indexes and
the handler column.

## 0111_memory

Memory for agents. `ka.memories` holds one entry per `(project_id, name)`: a lowercase path-like name, jsonb object
metadata, content (1 to 20000 characters), a generated `search_vector` (name split at `/`, content and metadata values,
via `ka.jsonb_values_text`), an optional `embedding` with its `embedding_profile` (both set or both null) and the
revision with who saved it (agent, session, actor). `ka.memory_versions` keeps earlier revisions. The `memory` tool
group is allowed in `ka.tools` and `memory` as a `ka.events` source. The down migration deletes memory events and
memory tools, restores both checks and drops both tables and the function.

## 0115_tools_always_on

Every run gets every tool (the whole catalog, its MCP servers' tools, every expert; `run_subagent` except in
subagents), so per-agent tool storage goes: `ka.agent_tools` (on/off, edited usage, `subagent_allowed`, the
`run_subagent` row behind May start subagents and a caller's `expert:<id>` choices) and `ka.tools.needs` are dropped.
The down migration recreates both empty with their original columns and constraints (0078, 0085, 0108); the rows are
not restored.

## 0120_schedule_emit_event

Schedules can emit an event: `ka.schedules.target_kind` also takes `event.emit` (`target_id` = the schedule's own id,
so each emit schedule is its own target) and `ka.events.source` also takes `schedule`. Each firing records one
`schedule.fired` event, deduplicated per firing. The down migration deletes `schedule` events and `event.emit`
schedules and restores both checks.

## 0121_schedule_names

Handlers bind to an Emit event schedule by its name, the `subject` of its `schedule.fired` events, so
`ka.schedules.name` is unique per project (`schedules_project_name_key`). A repeated name first gets ` (2)`, ` (3)`…
(the oldest row keeps it; a suffix already in use is skipped). Handlers whose type pattern matches `schedule.fired`
(`*`, `*.*`, `schedule.*`, `*.fired`, `schedule.fired`) and that check `payload.schedule_id` equals an emit schedule's
id now check `subject` equals that schedule's (possibly renamed) name; their revision goes up. A condition naming a
deleted schedule is left as it was. The down migration is best effort (test data): it rewrites `subject` equals an
emit schedule's name back to `payload.schedule_id` equals its id for those handlers and drops the constraint; renamed
duplicates keep their suffix.

## 0122_chat_sessions

`ka.agent_sessions.purpose` also takes `chat`: a saved agent's chat started with **Run agent** on the Agents page
(`POST /api/sessions` with `agent_id` and `purpose: "chat"`). Like `test` and `event` it needs its agent
(`agent_sessions_ad_hoc` is unchanged). The down migration turns chat sessions into Test sessions (`test`) and restores
the old check.

## 0123_session_wakeups

Agents can continue their own session later (wake-ups, see [agents](agents.md#tools)). `ka.agent_session_inputs` gains
`not_before timestamptz` (null: deliver as soon as possible, as before) and `kind text NOT NULL DEFAULT 'message'`
(`agent_session_inputs_kind`: `message` or `wakeup`); `agent_session_inputs_wakeup_time` requires `not_before` for a
wake-up, and the partial index `agent_session_inputs_wakeups` serves queued wake-ups by `not_before`. Existing inputs
become messages. The down migration deletes every wake-up row (pending and delivered; people's messages stay) and drops
the index, constraints and columns.

## 0124_one_time_schedules

One-time schedules (Administration → Schedules, Emit event only). `ka.schedules.cron` becomes nullable and
`run_at timestamptz` is added: a schedule with `run_at` runs once at that time; `schedules_one_timetable` requires
exactly one of `cron` and `run_at`, and `schedules_run_at_emit` allows `run_at` only for `target_kind` `event.emit`
(the Jira pull always repeats). After its scheduled run a one-time schedule is switched off (`enabled` false,
`next_run_at` null); the row and its `schedule_runs` stay. The down migration deletes one-time schedules (test data)
with their history, drops the constraints and the column and makes `cron` NOT NULL again.

## 0125_inventory_model

One inventory model (see [architecture](architecture.md#service-inventory-and-agent-context)). Every deployment is in
an environment: deployments with only a free-text `environment` label get the environment of that name
(case-insensitive, created when missing), then `service_deployments.environment_id` is `NOT NULL`, its foreign key no
longer unlinks deployments when an environment is deleted (an environment with deployments cannot be deleted), and
deployments get a `revision`. New tables: `ka.connections` (from a deployment to another deployment or to an external
host, with port, protocol, description, revision and who created it — person, or agent and session) and
`ka.service_relations` (`depends_on`, `part_of` to a service, `built_from` to a code project). Inventory links are
converted: `depends_on` and `built_from` become relations; `connects_to` between two services becomes one connection
per pair of their deployments in the same environment (port and protocol from the link's attributes when valid, else
the target deployment's port and `tcp`), or a `depends_on` relation when there is no such pair; `connects_to` links
with an end on a server, environment or code project are dropped. The old tables stay until 0126. The down migration
drops the new tables (connections and relations are lost; `inventory_links` still holds what it had) and the revision,
and makes `environment_id` nullable again; environments created for labels stay.

## 0126_inventory_drop_old

Drops what 0125 replaced: `environment_services`, `environment_servers` (membership comes from deployments; a server
or service that was a member without a deployment there is no longer in that environment), `inventory_links`, the
`inventory_entries`, `service_assets`, `server_assets` and `environment_assets` views (the inventory module reads the
tables), and the free-text `service_deployments.environment` with its unique (service, server, environment,
runtime_name). The deployment identity (service, server, environment_id, runtime_name) is no longer deferrable. The
down migration recreates them backfilled: the label from the environment's name, membership from deployments, links
from `depends_on` and `built_from` relations and one `connects_to` per pair of services with a connection (port and
protocol of the oldest as attributes). Lost on the way down: `part_of` relations and connections to external hosts
(0125's down drops them with their tables).

## 0129_service_proposals_gone

Service proposals give way to inventory drafts (0128): `discovery_proposals` keeps only document proposals. Service
proposals are deleted with the document proposals about them, and `service_proposal_id` with its constraints and index
is dropped. The seeded **Server discovery** prompt no longer proposes services with `propose_service` (gone): where
its active text is still 0106's seed, a new version becomes active that saves inventory findings as drafts with
`save_inventory_draft` (one call with the server, services, deployments, connections and relations; a person confirms
them in the environment workspace) and still proposes documents with `propose_document`; the rest of the prompt is
kept, and the seeded description follows. A prompt whose text a person changed is left alone (replace its
`propose_service` step yourself). New workspaces get the new text as version 1. The down migration restores the shape
(service proposals are not restored), removes the new prompt version where it is still the active, newest version with
0129's text (the version before it is active again), and restores 0106's seed for new workspaces; workspaces created
after 0129 keep the new text.

## 0130_draft_links

An agent's draft of a live document may hold `linked` links to servers, services, environments and code projects:
`update_document(..., resource=...)` stores the link with the draft, and promoting the draft adds it to the document
(`documents/store.py` `promote_draft`; a link the document already has stays as it is). The trigger
`document_resources_owner` now refuses only a `built_for` link on a draft of a live document (what a document was built
for stays the document's own) and also checks a changed `connection`. Brand-new drafts held their links before. The down
migration deletes the links drafts of live documents hold (they were never published) and restores the trigger that
refuses any link on such a draft.

## 0131_proposals_gone

Proposals are gone: agents draft documents (`new_document`, `update_document`, `edit_document`) and the inventory
(`save_inventory_draft`), and a person confirms or publishes. `ka.discovery_proposals` is dropped (pending proposals
are lost; accepted ones already made their drafts). The seeded **Server discovery** prompt (drafts the inventory, drafts
documents with the document tools, no `propose_document`), **Write a document** and **Revise a document**
(`save_document` is now `update_document`) each get a new version, but only where the active, newest version is still
the text 0129, 0101 or 0100 seeded; a prompt a person changed is left alone. The Server discovery description follows
where it is still the seeded one. New workspaces get the new texts as version 1. The down migration recreates the
table empty (in its 0129 shape) and removes the new prompt versions where they are still the active, newest,
untouched ones, restoring the earlier seeds for new workspaces.


## 0132_memory_settings

`ka.memory_settings` holds one row per project with the embedding model memory uses (`embedding_model_id`, set to NULL
when the connection is deleted). It is filled from `rag_workspaces.embedding_model_id`, so memory search keeps using the
model it used before. The down migration copies the setting back to the workspace and drops the table.

## 0133_document_search

`search_documents` searches the database: the partial GIN index `documents_search` on live documents (`NOT is_draft`)
over `setweight(to_tsvector('english', title), 'A') || … description 'B' || … markdown 'C'` (the `english`
configuration: stemmed, so "rotate" finds "rotation"; memory search keeps `simple`). The tool's query repeats the expression (`capabilities/document_tools.py` `SEARCH_VECTOR`) so the
index serves it. The "Answer only from documents" mode goes with the RAG reading tools: `ka.agents.documents_grounded`
is dropped. The down migration drops the index and adds the column back, off for every agent.

## 0134_rag_gone

RAG is gone: the service, its stack and its tables. Dropped: `rag_vectors`, `rag_chunks`, `rag_file_log`, `rag_files`,
`rag_collection_paths`, `rag_collections`, `rag_folders` and `rag_workspaces` (0132 moved the embedding model to
`memory_settings` first). Jobs of the kinds `rag_folder_index` and `rag_collection_sync` are deleted with their events
and questions, and `jobs.rag_folder_id`, `jobs.rag_collection_id`, their foreign keys, the index
`rag_collection_one_active_job` and the two kinds leave the checks and `guard_job_update`. `embedding_model_updated` no
longer bumps the workspace revision (the chat/embeddings check stays). `collection` entries are removed from stored
access: `agents.access`, `agent_sessions.access` and the access copy in `agent_sessions.agent_config`. The down
migration recreates the tables empty in their 0133 shape and restores the job columns, checks, index and both functions;
deleted jobs and access entries do not come back.

After upgrading, the `rag` and `rag-worker` containers of an earlier `make up` are orphans: stop them with
`docker compose --env-file .env -f deploy/compose.yaml down --remove-orphans` (or `make down` before pulling). The
`.env` keys `RAG_BACKOFFICE_KEY`, `GENERATED_DOCS_FOLDER` and `JIRA_FOLDER` are no longer read and can be removed, and
the `DATA_ROOT/generated-documents` and `DATA_ROOT/jira` folders are no longer used.

## 0135_agent_disabled_tools

Tool selection: `ka.agents.disabled_tools` (`text[]`, not null, default empty) holds the catalog tools never offered in a
run of the agent. Off tools are stored, not on tools, so a tool added to the catalog later is on for every agent. A run
may switch more off (the start request's `disabled_tools`, recorded in the pinned configuration); it cannot switch one
back on. The down migration drops the column: every agent is offered every tool again.

## 0136_output_schemas

The output schema library: `ka.output_schemas` (name, description, `current_version`, `revision`) and
`ka.output_schema_versions` (every saved version's JSON, kept). `ka.event_handlers` gains `output_schema_id` /
`output_schema_version` (no version: the current one; `RESTRICT`, so a used schema cannot be deleted), and
`ka.agent_sessions` gains `output`, `output_error`, `output_schema_id` and `output_schema_version`. Events take source
`run` (`run.output`); a run a handler started records its `run.output` in that event's chain (`parent_id`, `root_id`,
`depth`), so `events_source_made` allows a chain position for source `run` as well as `handler`. `ka.llm_models` gains
`capabilities` (`supports_structured_output`, on for OpenAI models), and handler runs may be `skipped`. The down
migration deletes `run` events and skipped handler runs, then drops the tables and columns and restores the checks.

## 0137_server_health_schema

A built-in output schema, **Server health check**, in every workspace that has no schema of that name: the server
checked, `overall_status` (`normal` / `worth_checking` / `issue`), a `summary`, and `findings` (one per problem:
`severity`, `category`, `service` or null, a stable `title`, `details`, `evidence`, `recommendation`; empty when
normal), for an agent that checks a server and a handler that turns each issue into a ticket. A workspace that already
has a schema of that name keeps its own. Workspaces created later do not get it (create it on the Output schemas page).
The down migration removes the seeded schema only where it is untouched (one version, the seeded JSON) and no handler
names it.

## 0138_previous_output

Include the previous output: `ka.agent_sessions` gains `schedule_id` (the schedule whose `schedule.fired` event is the
root of the run's event chain) and `handler_id` loses its foreign key — neither has one, so a run outlives a deleted
schedule or handler. The partial index `agent_sessions_previous_output` (`handler_id, schedule_id, created_at DESC`
where `output IS NOT NULL`) serves the lookup of a handler's last valid output. `ka.event_handlers` gains
`include_previous_output` (boolean, default false). Existing runs are backfilled: `handler_id` from
`ka.event_handler_runs` (also for a deleted handler), `schedule_id` from the root of their event's chain (or the
`root_id` the run pinned when the event is gone). The down migration drops the flag, the index and `schedule_id`, and
restores the `handler_id` foreign key (`ON DELETE SET NULL`), clearing the ids of handlers that no longer exist.

## 0140_code_project_prompt

Two built-in prompts in the prompt library of every workspace (seeded like the assistant prompts and Server discovery:
`seed_code_project_prompts`, run for the existing workspaces and by the `seed_code_project_prompts` trigger for new
ones; one version each, input kind `code`):

- **Code project assistant** (tags `assistant`, `code-project`; id `md5('knowledge-agent-v3:prompts:code-project-assistant:<workspace>')`):
  a chat on a code project's default prompt. It answers questions about the repository by reading only what answers
  them and drafts documents only when asked.
- **Code project overview** (tags `code-project-discovery`, `overview`): what **Discover…** offers on a code project. It
  reads the repository (structure, entry points, configuration, deployment files) and drafts its overview document
  with `new_document` (or `update_document` of an existing overview): purpose, structure, how to run and build,
  configuration, dependencies, deployment, where to look first.

The down migration removes each prompt only where it is unchanged (its one version, never saved again) and drops the
trigger and functions.

## 0141_skills_for_all

Skills leave the access model: every run gets every enabled skill of the project, pinned at start in its configuration
(`agent_config.skills`). The skill entries (`{"type": "skill", ...}`) are removed from stored access objects: saved
agents (`ka.agents.access`), runs (`ka.agent_sessions.access`) and the access copy in a run's `agent_config`, as 0139
did. Code also ignores such an entry when it reads an older object. The down migration restores nothing (the removed
entries are test data and do not come back).

## 0142_test_is_chat

One way to run a saved agent: its Test sessions become chats (`UPDATE ka.agent_sessions SET purpose = 'chat' WHERE
purpose = 'test'`), and their run records' start kind `test_session` becomes `chat_session`. `test` leaves the
`agent_sessions_purpose` check. The agent page's Test tab is now its Chat tab, and **Run agent** opens it. The down
migration restores the check only: chats stay chats (test data).

## 0143_code_project_dependencies

`ka.code_project_dependencies`: a code project depends on another code project (a library it imports).
`from_code_project_id` and `to_code_project_id` reference `ka.repositories` (with the project, `ON DELETE CASCADE`),
`description` (at most 4000 characters), `status` draft/confirmed like the other inventory tables (an agent's row is a
draft), `revision`, `created_by`, `created_by_agent`, `session_id` and timestamps. A pair is unique
(`code_project_dependencies_once`) and a code project never depends on itself (`code_project_dependencies_not_self`);
cycles are allowed and no transitive closure is stored. Services still relate to code only through `built_from`: the
services a library affects are derived (the services built from the code projects that depend on it). Written through
`inventory/store.py` (kind `code_dependency`: create, update, delete, confirm and discard; a draft needs nothing else and
nothing needs it). The down migration drops the table (test data).

## 0144_no_document_folders

Documents lose their folder, a leftover of RAG (nothing was written to disk): a document is named by its slug in the
project and organized by its resource links, tags and full-text search. Before the column goes, slugs that would
collide in a project are renamed: in each project and slug a live document keeps it before a brand-new draft (then the
oldest by `created_at`, then `id`), the others get the first free `-2`, `-3`, ... (the base shortened to stay within 120 characters). `documents.folder`, its
check and the `documents_folder_slug` index go; `documents_slug` is unique on `(project_id, slug) WHERE slug IS NOT
NULL` (live documents and brand-new drafts, as before), and `documents_kind` no longer mentions the folder. The views
`live_documents`, `document_details` and `document_drafts_view` are recreated without it. A new-document run loses its
folder too: `agent_sessions.folder` and its check go, and `agent_sessions_documentation` needs only the document name.
The down migration adds both columns back (nullable, at the end of their tables), restores the checks, the index and
the views; documents come back without a folder, renamed slugs keep their new names, and existing new-document runs
get the folder `documents` (test data).

## 0145_write_with_an_agent

Write with an agent names no document up front: the agent creates documents with `new_document`. A new-document run
(`agent_sessions.purpose = 'documents'`) no longer has a document name: `agent_sessions.document_name` and its check go,
and `agent_sessions_documentation` only keeps `draft`, `draft_turn` and `document_id` empty for other purposes. The
partial index `audit_events_draft_session` on `(details->>'session_id', created_at, id) WHERE action =
'document.draft.created'` serves the drafts of one conversation (`GET /api/documentation/drafts?session_id=`). The
seeded **Write a document** prompt gets a new version (create with `new_document`, change with `update_document` or
`edit_document`, a mentioned draft's id works), but only where its active, newest version is still the text 0131
seeded; its description follows where it is still the seeded one. New workspaces get the new text as version 1. The
down migration adds `document_name` back (at the end of the table, with its length check; existing documents runs get
their title, test data), restores the check, drops the index, and removes the new prompt version where it is still the
active, newest, untouched one, restoring the 0131 seed for new workspaces. A workspace created while 0145 was applied
keeps the 0145 text as its version 1 after down (only the seed function goes back; test data).
