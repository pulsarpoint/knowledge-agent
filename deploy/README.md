# Standalone v3 deployment

Run all commands from the v3 repository root. `make up` prepares root `.env`,
builds local images and starts `deploy/compose.yaml`. The Compose project name is
`knowledge-agent-v3`. Do not copy a v2 `.env` or reuse its data volumes.

## Service boundaries

- Administration enters through `admin-oauth2-proxy` on port 8183. The admin web
  server and API and `litellm` have no published host ports.
- The gateway uses the `knowledge-agent-v3-admin` Keycloak client, its own client
  secret and cookie name. It also requires the `docs-admins` group, which the admin API checks independently.
  Browser login uses the public URL, while token/userinfo/JWKS calls use Docker
  service DNS. Issuer validation remains enabled. This follows the documented
  [explicit OIDC endpoint configuration](https://oauth2-proxy.github.io/oauth2-proxy/configuration/overview/)
  and Keycloak's [backchannel hostname configuration](https://www.keycloak.org/server/hostname).
- `migrate` runs the pinned golang-migrate v4.19.1 CLI after PostgreSQL becomes
  healthy and records state in `public.golang_schema_migrations`. Application
  startup requires successful migration. Existing checksum-ledger databases need
  the one-time verified adoption procedure in the [migration guide](../docs/migrations.md).
  Repository settings, encrypted
  tokens and pull jobs use PostgreSQL.
- Each `admin-api` process keeps one PostgreSQL connection open for `LISTEN ka_sessions` (live session pages, see
  [documentation](../docs/documentation.md)); it must reach PostgreSQL directly, not through a transaction-pooling
  proxy, which does not deliver notifications. On shutdown it waits at most 5 s for open session streams.
- `repository-worker` has no host port and owns the writable `CODE_FOLDER` and
  `CODE_INDEX_FOLDER` binds; everything else sees `DATA_ROOT` read-only at `/work/data`.
  It claims PostgreSQL jobs, clones and periodically pulls Git source into named folders.
- `jira-mock` is an optional local test profile. `make jira-mock-up` starts 75
  synthetic tickets on an internal Docker network shared with `admin-api` and
  `repository-worker`, plus a control page on `127.0.0.1:${JIRA_MOCK_PORT:-8184}` (loopback only)
  to edit tickets and see the comments Knowledge Agent posts. Its state is kept in the
  `jira-mock-data` volume. `make jira-mock-down` stops it. See the
  [Jira mock guide](../docs/jira.md) for test credentials and failure scenarios.
- `agent-session-worker` runs native agents' **Test**, **Ask agent**, new-document and event-handler sessions with the native runtime. It is on `default`
  (PostgreSQL, MCP servers, model providers, SSH hosts) and `agent-shell` (`shell-worker`), sees `DATA_ROOT` read-only
  and shares `agent-workspaces` with `shell-worker`. At most `AGENT_SESSION_MAX_SESSIONS` (default 4) sessions run at once.
  Native turns have no time or model-call limit; Stop ends a turn within about 30 seconds. A turn runs at most `AGENT_MAX_SUBAGENTS` (default 4) subagents or expert calls at once, and at most 16 in all; an expert's own subagents run in slots of their own, so a turn can run up to `AGENT_MAX_SUBAGENTS` x (1 + running experts) helpers at once.
- Phoenix has its own authenticated UI and volume. The admin API exports
  OpenInference traces to it (project `docs-wiki`); tracing failure does not stop agent runs.
- Each service receives an explicit subset of environment variables. The complete
  root `.env` is never mounted into a container or included in a Docker image.

## Data folders

The application keeps its files in one host folder that you choose, `DATA_ROOT`.
Set it once; it is recorded in `.env` and later `make setup`/`make up` runs reuse it:

```bash
sudo mkdir -p /opt/knowledge-agent && sudo chown "$(id -u):$(id -g)" /opt/knowledge-agent
make setup DATA_ROOT=/opt/knowledge-agent
```

Compose refuses to start without it (`Run make setup DATA_ROOT=/path`); nothing is
created inside the checkout. `DATA_ROOT` must be an absolute path. `make setup` creates
the folders below inside it. `DATA_ROOT` is mounted once, read-only, at `/work/data`;
each folder is writable only in the services listed.

| Variable | Default | Container path | Contents | Writable in |
| --- | --- | --- | --- | --- |
| `DATA_ROOT` | (required) | `/work/data` | Everything below; read-only base | nothing |
| `CODE_FOLDER` | `code` | `/work/data/code` | Code projects, Git clones and caches | `admin-api`, `repository-worker` |
| `CODE_INDEX_FOLDER` | `code-index` | `/work/data/code-index` | Code graph snapshots | `admin-api`, `repository-worker` |
| `DOCS_FOLDER` | `docs` | `/work/data/docs` | Bootstrap docs seeded from `examples/docs` | nothing |
| `SKILLS_FOLDER` | `skills` | `/work/data/skills` | Skill folders (`<skill id>/<version>/`), see [skills](../docs/agents.md#skills) | `admin-api` |

Each `*_FOLDER` value is a path relative to `DATA_ROOT`: no leading `/`, no `..`, no
hidden or empty segments, and no folder inside another one. `make setup` stops and
names the variable when a value breaks these rules.

Every service sees all of `DATA_ROOT`, so it should hold only the configured folders;
`make setup` warns about any other entry (hidden `.DS_Store` files are ignored). Keep
backups and logs in a sibling folder such as `DATA_ARCHIVE`, not inside `DATA_ROOT`.
Admin -> Storage shows the host and container path of each folder.

**Upgrading from per-folder overrides:** `CODE_PATH`, `CODE_INDEX_PATH` and `DOCS_PATH`
are refused by `make setup`, which names the replacement variable. An override inside
`DATA_ROOT` translates to the relative value (`CODE_PATH=$DATA_ROOT/sources/code` becomes
`CODE_FOLDER=sources/code`); one outside must be moved into `DATA_ROOT` first.

**Moving the data:** `make down`, then copy only the configured folders into the new
`DATA_ROOT`, not everything around them (backups, logs and user state stay outside):

```bash
mkdir -p /new/knowledge-agent
for folder in code code-index docs skills; do
  rsync -a "/opt/knowledge-agent/$folder/" "/new/knowledge-agent/$folder/"
done
```

Set `DATA_ROOT` in `.env` (`make setup DATA_ROOT=/new/knowledge-agent`), then `make up`.

## Data and lifecycle

| Location | Contents |
| --- | --- |
| `DATA_ROOT` host folder | Code, code graphs, bootstrap docs and skills (see above) |
| `knowledge-agent-v3_postgres-data` | V3 application data: documents, Jira tickets, memory (with vectors), agents, sessions and jobs |
| `knowledge-agent-v3_keycloak-data` | Independent realm, users and login configuration |
| `knowledge-agent-v3_phoenix-data` | Independent Phoenix traces and accounts |

**Administration → Storage** shows configured host/container paths for `DATA_ROOT` and its folders, their availability and a read-only directory/file browser. Documents and their versions live in PostgreSQL, not in a folder.

`make down` retains volumes. Back up these volumes and the configured content
folders before migrating real data. Credentials in root `.env` are required to
reconnect to the existing stores. Deleting/regenerating them is not a password
rotation procedure for already initialized Keycloak, Phoenix or PostgreSQL.
The `CREDENTIAL_ENCRYPTION_KEY` must also be retained to decrypt saved credentials.
Keep it separate from the PostgreSQL backup. The administration API, repository
worker and documentation worker receive this key; it is never sent to the browser or included in agent prompts.

## Private Git repositories and automatic pulls

In **Code projects → Add project → Git repository**, enter a clean HTTPS URL and
enable **Private repository** to supply a Git username and token. This selects
authenticated access; it does not change visibility on the Git provider.
Both values are encrypted together with AES-GCM in `ka.credentials` and are never
returned by the API. Edit fields start blank: omitting either keeps its saved
value. Switching private access off and saving removes both saved values.

The worker decrypts credentials for the pull and supplies them through `GIT_ASKPASS`
using a dedicated subprocess environment. Credentials are not embedded in command
arguments, saved remote URLs or `.git/config`. Pull failures expose a safe error
message rather than raw Git output. New encrypted payloads use format `v2`; the
worker still accepts existing `v1` token-only records. No database schema change
or re-entry of existing tokens is required. Deploy the API and repository worker
together when updating this format.

**Automatic pulls** stores `sync_interval_minutes` and `next_sync_at` in PostgreSQL.
The `repository-worker` Docker service checks for due repositories every two seconds
while idle. It queues a `repository_sync` job and advances the next due time by the
configured interval (60 minutes by default in the form). It then claims and executes
queued jobs, one at a time per worker. A queued/running pull prevents a duplicate
job for that repository. Closing the browser has no effect; stopping the worker
pauses pulls and overdue repositories are picked up after it restarts. Busy workers
can delay the actual start. Manual pulls also advance the next scheduled time.

The same service also runs due **schedules** (Administration → Schedules, e.g. the Jira auto pull) on a second thread:
it checks every 5 seconds, leases a schedule for 15 minutes while it runs, and finishes a running pull before it stops.

The Keycloak image uses its original development/H2 mode, and the local gateway
uses HTTP cookies because all host ports bind to loopback. Network deployments
need explicit TLS, hostname, cookie and Keycloak production configuration before
changing those bindings.

## Local application administrators

The application administrator is `v3-admin`, created by the Keycloak realm import on the
first boot with the password `APP_ADMIN_PASSWORD` from `.env`. Add further
administrators in the Keycloak console (realm `docs`) by adding a user to the
`docs-admins` group; this is an application administrator, not a Keycloak server
administrator. Check another account with
`python3 scripts/smoke.py --credentials <file> --auth-only`, where the file is JSON
`{"username": …, "password": …}` kept outside the repository.

The default `make smoke` uses
the bootstrap application admin. Disabling/removing accounts, changing groups,
or other user administration is available through the local Keycloak console.

## Configuration and troubleshooting

Change ADMIN_PORT, KEYCLOAK_PORT, PHOENIX_PORT, POSTGRES_PORT and JIRA_MOCK_PORT in root `.env` if a
port is occupied. Set public WEB_HOST consistently; for an already imported
realm, `make up` applies the client redirect URIs to match these settings.

```sh
make compose-validate
make status
docker compose --env-file .env -f deploy/compose.yaml logs --tail=80 keycloak admin-oauth2-proxy
```

The smoke check uses the bootstrap application admin. If its password has changed
in Keycloak, update APP_ADMIN_PASSWORD to the current credential before running the
check. Smoke does not issue an AI prompt or verify provider credentials.

For short private hostnames used by SSH or Jira, set `DOCKER_DNS_SEARCH` in root `.env` to the host's DNS search domain (for Tailscale, the tailnet's `tailXXXX.ts.net` domain). Compose applies this to the admin API, where those outbound connections run. Recreate the container with `docker compose --env-file .env -f deploy/compose.yaml up -d --no-deps --wait admin-api`. Docker retains its normal DNS resolver and internal service discovery. With no domain configured, the search suffix is `.`.

This fixes short-name lookup when the full hostname already resolves inside Docker. It does not copy the host's `/etc/hosts`, SSH aliases, or VPN routing configuration. If the full name also fails, fix the Docker/host DNS forwarding or use a reachable DNS server for that domain. See [Compose dns_search](https://docs.docker.com/reference/compose-file/services/#dns_search) and [Tailscale MagicDNS](https://tailscale.com/docs/features/magicdns).

To run native services, use the service guides' `uv run` commands. The admin Vite
proxy on port 5174 expects its API on `127.0.0.1:8766`. The authenticated Docker
entry point uses 8183. Do not expose the native internal APIs as a
substitute for the authenticated gateway.

## Deploy and maintain administration

```sh
# Rebuild just the admin frontend/API after their source changes.
docker compose --env-file .env -f deploy/compose.yaml up -d --build admin-api admin-web repository-worker admin-oauth2-proxy
# Stop administration.
docker compose --env-file .env -f deploy/compose.yaml stop admin-oauth2-proxy admin-web admin-api repository-worker
```

The `keycloak-configure` one-shot service applies the `knowledge-agent-v3-admin`
client definition after Keycloak becomes healthy and deletes the retired Reader
client `knowledge-agent-v3` if a persisted realm still has it. It preserves
existing users and application data. Admin group claims come from the dedicated client mapper;
the gateway explicitly requests `openid email profile` to avoid requesting a
nonexistent `groups` scope.

The admin API owns its configuration (`knowledge_admin/config.py`). New PostgreSQL
management workflows belong in `services/admin-api`.

## Upgrading after the Reader removal

The end-user Reader application (`docs-wiki`, `web` and `oauth2-proxy` on port 8180)
was removed. After pulling this version on an existing installation:

1. Run the database migration (`make up` runs it, or `make db-migrate`). Migration
   0071 deletes Reader conversations and drops `ka.reader_settings`,
   `agent_sessions.channel` and `agent_sessions.hidden_by_owner_at`.
2. Remove the orphaned containers:
   `docker compose --env-file .env -f deploy/compose.yaml up -d --remove-orphans`
   (or run `make up`, then remove the `docs-wiki`, `web` and `oauth2-proxy` containers).
3. Optionally back up, then delete, the old `wiki-data` volume (Reader gaps and chat
   files): `docker volume rm knowledge-agent-v3_wiki-data`.
4. `keycloak-configure` deletes the retired `knowledge-agent-v3` Keycloak client
   automatically.
5. Delete the obsolete `.env` keys `WEB_PORT`, `READER_AGENT_KEY`,
   `OAUTH2_PROXY_CLIENT_SECRET` and `OAUTH2_PROXY_COOKIE_SECRET`.

### Development databases that applied `0071_reader_agents`

An earlier, unreleased build had a different migration `0071_reader_agents`. A database
that applied it is already at version 71, so `0071_remove_reader` never runs. Fix it by
hand once (back up first), in `psql` as `knowledge_agent`:

```sql
SET search_path = ka, public;
DELETE FROM agent_run_events e USING operations_runs r, agent_sessions s
    WHERE e.run_key = 'run:' || r.id AND r.session_id = s.id AND s.channel = 'reader';
DELETE FROM operations_commands c USING operations_runs r, agent_sessions s
    WHERE c.run_id = r.id AND r.session_id = s.id AND s.channel = 'reader';
DELETE FROM operations_runs r USING agent_sessions s WHERE r.session_id = s.id AND s.channel = 'reader';
DELETE FROM agent_session_reviews v USING agent_sessions s WHERE v.session_id = s.id AND s.channel = 'reader';
DELETE FROM agent_sessions WHERE channel = 'reader';
DROP INDEX IF EXISTS agent_sessions_reader_owner;
ALTER TABLE agent_sessions DROP COLUMN channel, DROP COLUMN hidden_by_owner_at;
ALTER TABLE agents DROP COLUMN reader_enabled;   -- added by 0071_reader_agents
DROP TABLE IF EXISTS reader_settings;            -- 0071_reader_agents already dropped it
```

The recorded migration version stays 71.

## Upgrading to Generate documents

Docs → Generate documents now runs an agent session; the one-shot generation and its worker are gone. After pulling this version on an existing installation:

1. Run the database migration (`make up` runs it, or `make db-migrate`). Migration 0073
   deletes the one-shot generation's workflows, jobs, job events and run records;
   documents, versions and exports stay.
2. Remove the orphaned `documentation-worker` container:
   `docker compose --env-file .env -f deploy/compose.yaml up -d --remove-orphans`
3. Documentation agents need a Claude model and a documentation prompt of their own
   (Docs → Prompts holds the earlier prompts to copy from).

Back up the database first: rolling 0073 back restores only the empty tables, not the deleted history. It also drops
`documents.phase_metadata`, so a document written by a service phase re-exports to `<slug>/` instead of
`services/<service>/<phase>/`; applying 0073 again does not bring the phase folders back.

## Upgrading to native agent sessions

Generate documents now also runs native agents. After pulling this version on an existing installation:

1. Run the database migration (`make up` runs it, or `make db-migrate`). Migration 0074 adds three columns to
   `agent_sessions_cc` (`runtime`, `agent_config`, `history`; renamed `agent_sessions` by 0084); existing sessions are kept.
2. Start the new service:
   `docker compose --env-file .env -f deploy/compose.yaml up -d agent-session-worker`

Rolling 0074 back refuses while native sessions exist: delete them first.

## Upgrading to the removal of the multi-agent board

The multi-agent board and the coordinator are gone; every agent is an ordinary agent. After pulling this version on an
existing installation, run the database migration (`make up` runs it, or `make db-migrate`).

- Migration 0077 deletes the coordinator agent and the board's tables and history (`agent_board_tasks`,
  `agent_board_members`, `coordinator_settings` and the `board:*` run records). Back up the database first: rolling
  0077 back is not a restore.
- Rolling back below 0077 restores only the empty schema; the deleted coordinator agent, board tasks and history do
  not come back.
- A rollback that stops exactly at version 30 cannot be upgraded again. Roll back to 29 or below, or stay at 31 or
  above.
- OpenAI Chat Completions models can no longer be assigned to any agent, because only the coordinator could use them.
  They remain usable as the Search model in LLM Settings.
- `PHOENIX_PUBLIC_URL` is no longer read; remove it from `.env`.

## Upgrading to the removal of Claude Code agents

Claude Code agents and the `claude-code-worker` are gone; every agent is a native agent and Generate documents runs
native agents through `agent-session-worker`. After pulling this version on an existing installation:

1. Run the database migration (`make up` runs it, or `make db-migrate`). Migration 0084 converts Claude Code agents
   to native agents with the tools of their enabled capabilities, deletes Claude Code sessions with their inputs and
   reviews, and renames `ka.agent_sessions_cc` to `ka.agent_sessions`. Saved documents stay, and the run records of
   the deleted sessions stay readable in Agent runs.
2. Remove the orphaned `claude-code-worker` container and its network:
   `docker compose --env-file .env -f deploy/compose.yaml up -d --build --remove-orphans`
   (`make up` does not remove orphans, so the old worker would keep running with database secrets).
3. `CLAUDE_CODE_MAX_SESSIONS` and `CLAUDE_CODE_CLI` in `.env` are no longer used; delete them.
4. Verify: `docker ps` lists no `claude-code-worker`, and `docker network ls` lists no `*_claude-code` network.

Back up the database first: rolling 0084 back does not restore the deleted Claude Code sessions.

## Upgrading to clones instead of versions

An agent now has one configuration, and its system prompt is a field on the agent. After pulling this version on an
existing installation, run the database migration (`make up` runs it, or `make db-migrate`). Back up the database
first.

- Migration 0079 adds `agents.system_prompt`, filled from each agent's active prompt version.
- Migration 0080 makes each agent's active configuration version its configuration (including its tools, overrides
  and connections, and its name and description when no other agent has that name), then deletes configuration
  versions and prompt history. Resources are referenced live: frozen copies of resource details from a version are
  not kept, and items deleted since are dropped. One audit event `agent.version.materialized` per migrated agent
  keeps the version's configuration. In an installation without exactly one application project, agents are skipped
  and keep their own configuration. If the model of an agent's active version was deleted, the agent ends with no
  model: choose one for it before it runs.
- Conversations and runs keep the configuration they ran with.
- Rolling back below 0080 restores only the empty schema: versions and prompt history do not come back. Rolling back
  0079 gives each agent its current prompt as version 1 (an edited prompt becomes a new active version; text is
  truncated to 40 000 characters).

## Upgrading to Test sessions

The agent **Test** tab now runs its conversations as sessions in `agent-session-worker`, like Generate documents
sessions (Stop, messages while the agent works, no per-turn time limit). After pulling this version on an existing
installation, run the database migration (`make up` runs it, or `make db-migrate`) and make sure
`agent-session-worker` runs. Back up the database first.

- Migration 0081 deletes the existing Test chats and their saved suggestions (`ka.agent_sessions`,
  `ka.operations_runs`, `ka.operations_commands`, `ka.agent_session_reviews`). Their run records are kept: Activity →
  Agent runs still lists them (type *Agent test*) and opens each full record.
- New Test sessions are listed in the Test tab's **Earlier runs** and in Agent runs as *Test session*; each answer's
  **Full record** opens `cc:<session>:<turn>`.
- Test sessions close after 24 hours without activity (the old Test chats closed after 1 hour) and have no 300-second
  turn limit.
- A saved credential that cannot be decrypted no longer refuses the start: the first turn fails with the worker's
  message.
- Header and per-turn model calls are not shown for sessions started before native sessions.
- `POST /api/agents/{id}/runs` and the other `/api/agents/{id}/runs*` routes are gone, as are
  `GET /api/agents/{id}/sessions/{session_id}` and `POST …/close` of the old Test chats; use
  `POST /api/sessions` with `agent_id` and the other session routes under `/api/sessions`. The later
  `POST` and `GET /api/agents/{id}/sessions` and `…/{session_id}/review` are gone too: use `POST /api/sessions`,
  `GET /api/sessions?agent_id=…` and `POST /api/sessions/{id}/review`.
- Rolling back 0081 deletes the Test sessions (their run records stay) and restores the old Test chat tables empty;
  the Test chats deleted by the upgrade do not come back.

## Upgrading to the session event hub

Live session pages (Generate documents, Test) no longer poll the database. After pulling this version on an existing
installation, run the database migration (`make up` runs it, or `make db-migrate`).

- Migration 0083 adds triggers that send `pg_notify('ka_sessions', …)` on changes to session events, sessions and
  queued messages. It changes no data.
- Each `admin-api` process listens on one dedicated connection; connect it directly to PostgreSQL (no
  transaction-pooling proxy). A lost notice or connection delays a page by at most 30 s.
- Restarting `admin-api` closes open streams after at most 5 s; browsers reconnect on their own.
- Rolling back 0083 drops the triggers, and then session pages hear of nothing. The polling code is gone from this
  version, so rolling back requires the previous app version as well.

## Upgrading to agent experts

After pulling this version, run the database migration (`make up` runs it, or `make db-migrate`). Migration 0085
adds `ka.agents.expert` and an index (built inside the migration transaction, so on a large installation it blocks writes
to agent run events for the few seconds the build takes); it changes no existing data, and no agent is an expert until you turn on **Offer
as an expert**. Generate documents sessions created after the upgrade get `run_subagent` and every expert; earlier
sessions keep their configuration. The per-turn limit is now 16 helpers (subagents and expert calls together).

- Rolling back 0085 deletes callers' `expert:<id>` rows (their choices of experts to switch off) and the expert switch.
  No other data is lost.

## Upgrading to agent tasks and Notifications (superseded by event runs, 0103)

After pulling this version, run the database migration (`make up` runs it, or `make db-migrate`). Migration 0090 adds
`ka.agent_tasks` and `ka.notifications`.

- Event handlers with the action Send to agent now queue an agent task instead of starting a session:
  `repository-worker` queues the tasks and `agent-session-worker` runs them, so update both together (`make up` does).
- Sessions that handlers started before 0090 stay viewable.

## Upgrading to event runs

After pulling this version, run the database migration (`make up` runs it, or `make db-migrate`). Migration 0103 adds
`event_id` and `handler_id` to `ka.agent_sessions` and `session_id` to `ka.notifications`.

- Event handlers with the action Send to agent now start an ordinary agent run, not an agent task. `repository-worker`
  starts the run and `agent-session-worker` runs it, so update both together (`make up` does).
- `agent-session-worker` no longer runs agent tasks, and `AGENT_TASK_MAX` does nothing. Remove it from your `.env`;
  a leftover value is ignored.
- Agent tasks still queued or running when 0103 ran are failed with a notification each. Send their events again.
  Old tasks stay visible as history.

## Upgrading to documents with drafts, archive and resource links

After pulling this version, run the database migration (`make up` runs it, or `make db-migrate`). Migration 0104 moves
drafts into `ka.documents`, earlier versions into `ka.document_archive` and document links into
`ka.document_resources`.

- Rebuild and restart admin-api, agent-session-worker, repository-worker and admin-web together (`make up` does): the
  old code reads tables 0104 drops.
- Open drafts are kept; promoted and discarded drafts are not carried over (their versions and audit rows stay).
  Promoting a draft now deletes the document's other drafts, and discarding deletes the draft.
- `GET /api/documentation/documents/{id}/versions/{version}` takes a version number, not a version id; responses no
  longer carry `version_id`, `current_version_id` or `published_version_id`. Update scripts that used them.
- Existing links are ordinary links. Links of documents created from now on are "built for" their resource and cannot
  be removed.
- Rollback of 0104 refuses once any document has a link with provenance or history (built for it, added or removed
  after the upgrade); restore the database backup instead.

## Upgrading to the system graph

After pulling this version, run the database migration (`make up` runs it, or `make db-migrate`). Migration 0092 adds
the system graph tables (Environments → System map).

- The admin-api image now contains `d2` v0.9.0 (with the TALA, ELK and dagre layouts) to draw System map diagrams;
  rebuild admin-api (`make up` does). The build downloads the release from GitHub and checks its sha256.
- Without `d2` (for example admin-api running outside Docker), the Diagram view says "d2 is not installed." and the
  Mermaid, D2 and JSON views still work.

## Upgrading to the inventory map

Migration 0095 replaces the stored system graph with connections between inventory entries (Environments → System map).

- Rows of `service_dependencies` and `service_repositories` move into `inventory_links`; documents attached to graph
  nodes move to the entry behind the node. Hand-drawn graph nodes and their attachments are gone (the documents stay).
- The rollback refuses while `connects_to` links, link attributes, `built_from` descriptions or attachments exist;
  remove them first.

## Upgrading to the inventory sync

Migration 0093 links the system graph to the inventory. When admin-api starts it gives every server, service,
deployment, environment and code project a node (Environments → System map) and from then on keeps them in step whenever
the inventory changes. Nodes made by hand keep their names; a synced node whose name is already taken gets " (2)".

## Upgrading to agent-written new documents

After pulling this version, run the database migration (`make up` runs it, or `make db-migrate`). Migration 0101 seeds the
prompt **Write a document** and closes the open Generate documents chats ("Closed by an update; start a new chat.").

- A new document is written with an agent from Documents → New → **Write with an agent**.
- Docs → Generate documents (`/documentation/generations`), `POST /api/generations/sessions` and
  `POST /api/generations/context` are removed and now return 404. Update any script that called them to use
  `POST /api/sessions` with `started_from.new_document`.
- A run's final answer no longer becomes its draft; the agent saves with `save_document`. Custom prompts tagged
  `documentation` should say so; copy the rules from **Write a document**.
- Earlier Generate documents runs stay readable at `/sessions/<id>` and in Activity → Agent runs.

## Upgrading to discovery in the server page's chat

After pulling this version, run the database migration (`make up` runs it, or `make db-migrate`). Migration 0102 updates
the untouched **Server discovery** prompt to version 2 and closes open discovery runs ("Closed by an update; start a new
chat.").

- A discovery run starts from **Ask agent** on a server's page, through `POST /api/sessions` with `started_from.server_id`.
- `POST /api/servers/{id}/discovery` and `GET /api/servers/{id}/discovery` are removed and now return 404. Use
  `POST /api/sessions` and `GET /api/sessions?server_id=`. The separate discovery run page is gone; run links
  `/servers/:id/discovery/:runId` open the server page on that run.
- The code no longer adds a discovery system prompt or a "short report" line. A customized **Server discovery** prompt
  should say: read the inventory first, save inventory findings as drafts with `save_inventory_draft` (since 0129;
  `propose_service` is gone) and draft documents with `new_document`, `update_document` and `edit_document` (since
  0131; `propose_document` is gone), correct and retry when a draft or a document is refused, finish with a short
  report.
- Every run's inventory tools and `get_document` now answer only about what the run was given.

## Upgrading to inventory drafts

After pulling this version, run the database migration (`make up` runs it, or `make db-migrate`). Agents write the
inventory as drafts (`save_inventory_draft` and the `save_*` tools); a person confirms or discards them in the
environment workspace. Migration 0129 deletes the service proposals (`propose_service` is gone) and gives the untouched **Server
discovery** prompt a new version that saves inventory findings with `save_inventory_draft`, in one call with the whole
picture. A **Server discovery** prompt whose text was changed is left alone: replace its `propose_service` step with
`save_inventory_draft` yourself.

## Upgrading to documents without proposals

After pulling this version, run the database migration (`make up` runs it, or `make db-migrate`). Migration 0131 drops
`ka.discovery_proposals`: proposals are gone (pending ones are lost, accepted ones already made their drafts). Agents
draft documents with `new_document`, `update_document` and `edit_document`, and a person publishes them on the
document's page. The **Propose…** wizard is now **Discover…**: it starts a run from a library prompt, and the
Proposals pages (`/servers/:id/proposals`, and the same for services and environments) and `/api/discovery/…` are
removed. The seeded **Server discovery**, **Write a document** and **Revise a document** prompts get a new version
that names the new tools (`save_document` is now `update_document`), only where their text is still the seeded one;
a prompt whose text was changed is left alone: replace `propose_document` and `save_document` in it yourself.
