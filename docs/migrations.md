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
