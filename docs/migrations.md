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

`scripts/migrate_agent_session_history.py` reads the old `ka.agent_sessions` table, so it applies only to databases
before 0081.

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
