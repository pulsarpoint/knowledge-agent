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

Test chats become sessions run by agent-session-worker. `ka.agent_sessions_cc` gets `purpose` (`documents` for Generate
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
