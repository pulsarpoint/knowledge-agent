# Standalone v3 deployment

Run all commands from the v3 repository root. `make up` prepares root `.env`,
builds local images and starts `deploy/compose.yaml`. The Compose project name is
`knowledge-agent-v3`. Do not copy a v2 `.env` or reuse its data volumes.

## Service boundaries

- Administration enters through `admin-oauth2-proxy` on port 8183. The admin web
  server and API, `rag` and `litellm` have no published host ports.
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
- `repository-worker` has no host port and owns the writable `CODE_FOLDER` and
  `CODE_INDEX_FOLDER` binds; everything else sees `DATA_ROOT` read-only at `/work/data`.
  It claims PostgreSQL jobs, clones and periodically pulls Git source into named folders.
- `jira-mock` is an optional local test profile. `make jira-mock-up` starts 75
  synthetic tickets on an internal Docker network shared with `admin-api`, with
  no host port or external connection. `make jira-mock-down` stops it. See the
  [Jira mock guide](../docs/jira.md) for test credentials and failure scenarios.
- `claude-code-worker` runs **Generate documents** sessions with the Claude Agent SDK.
  It is on its own `claude-code` network, shared only with `postgres` (not internal, so
  it keeps internet egress to Bedrock or Anthropic); Bash in a session cannot reach
  `admin-api`, `admin-web`, `rag`, `litellm`, Keycloak, the proxies or Phoenix. It sees
  `DATA_ROOT` read-only; its only writable mount is the `claude-code-home` volume at
  `/home/claude`. The worker runs as root (`cap_add: SETUID, SETGID, CHOWN`) to keep the
  database password and encryption key to itself. Each running session holds one worker
  slot and its Claude Code process runs as that slot's user, `claude1`..`claude8`
  (uids 1001-1008, group `claude`, gid 1001), with the session's own HOME,
  `/home/claude/sessions/<session id>` (0700). A later turn may run in another slot: the
  CLI wrapper hands the session's HOME (and its transcript, for resume) to the new
  slot's user. When a session leaves its slot, the worker kills the slot user's
  processes, removes its `/dev/shm` entries and takes the HOME back for root before
  the slot is reused (SysV IPC objects are not removed: an accepted residual). At most 8 sessions run at once (`CLAUDE_CODE_MAX_SESSIONS`, default 4).
  Files under `DATA_ROOT` must be readable by the claude users: world-readable, or
  group-readable by gid 1001.
- `agent-session-worker` runs native agents' **Generate documents** sessions with the native runtime. It is on `default`
  (RAG, PostgreSQL, MCP servers, model providers, SSH hosts) and `agent-shell` (`shell-worker`), sees `DATA_ROOT` read-only
  and shares `agent-workspaces` with `shell-worker`. At most `AGENT_SESSION_MAX_SESSIONS` (default 4) sessions run at once.
  Native turns have no time or model-call limit; Stop ends a turn within about 30 seconds.
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
| `GENERATED_DOCS_FOLDER` | `generated-documents` | `/work/data/generated-documents` | Current Markdown/D2 export of each document: `<slug>/`, `<folder>/<slug>/` or `services/<svc>/<phase>/`; indexed by the `documents` collection (old `v<n>/` folders are excluded) | `admin-api` |
| `JIRA_FOLDER` | `jira` | `/work/data/jira` | Jira exports, one `<KEY>.md` per ticket | `rag` |

Each `*_FOLDER` value is a path relative to `DATA_ROOT`: no leading `/`, no `..`, no
hidden or empty segments, and no folder inside another one. `make setup` stops and
names the variable when a value breaks these rules.

RAG sees all of `DATA_ROOT`, so it should hold only the configured folders and folders
you add for RAG on purpose; `make setup` warns about any other entry (hidden
`.DS_Store` files are ignored). Keep backups and logs in a sibling folder such as
`DATA_ARCHIVE`, not inside `DATA_ROOT`. Admin -> Storage shows the host and container
path of each folder.

**Upgrading from per-folder overrides:** `CODE_PATH`, `CODE_INDEX_PATH`, `RG_DATA`,
`GENERATED_DOCS_PATH`, `DOCS_PATH` and `RAG_SOURCE_ROOT` are refused by `make setup`,
which names the replacement variable. An override inside `DATA_ROOT` translates to the
relative value (`CODE_PATH=$DATA_ROOT/sources/code` becomes `CODE_FOLDER=sources/code`);
one outside must be moved into `DATA_ROOT` first. `make setup` moves
`DATA_ROOT/rag-data/jira` to `JIRA_FOLDER` once (an empty `JIRA_FOLDER` is replaced; when
both hold files, setup stops and asks you to merge them by hand), and migration `0067` rewrites the RAG
paths (it assumes the default `JIRA_FOLDER`; with another value the collection shows
**Folder missing** until its folder is replaced). Other `rag-data/...` folders are not
configured folders: move them out of `DATA_ROOT`, and a collection that used them shows
**Folder missing**. Migration `0067` also rewrites the container paths in saved agent
versions for the default folder names; with custom `*_FOLDER` values, publish each agent
version again.

**Upgrading to RAG collections (migration `0063`):** after `make up`, admin-api
re-exports every document whose current export is missing into `generated-documents`
(background, idempotent, failures only logged). Every collection then shows **Needs
refresh** in Admin → RAG → Collections; **Refresh** each one (or **Refresh all stale**).
Expect a full re-parse and, with embeddings on, a full re-embed (for example about 975
chunks for `jira`). Until a collection is refreshed its previous index keeps serving search.

**Moving the data:** `make down`, then copy only the five configured folders into the new
`DATA_ROOT`, not everything around them (backups, logs and user state stay outside). With
the default names and an old `.local` layout:

```bash
mkdir -p /opt/knowledge-agent
for folder in code code-index docs generated-documents; do
  rsync -a ".local/$folder/" "/opt/knowledge-agent/$folder/"
done
rsync -a .local/rag-data/jira/ /opt/knowledge-agent/jira/   # rag-data/jira becomes jira
```

Set `DATA_ROOT` in `.env` (`make setup DATA_ROOT=/opt/knowledge-agent`), then `make up`.

## Data and lifecycle

| Location | Contents |
| --- | --- |
| `DATA_ROOT` host folder | Code, code graphs, Jira exports, document exports and bootstrap docs (see above) |
| `knowledge-agent-v3_postgres-data` | V3 application metadata, processing jobs, searchable text and vectors |
| `knowledge-agent-v3_keycloak-data` | Independent realm, users and login configuration |
| `knowledge-agent-v3_phoenix-data` | Independent Phoenix traces and accounts |

**Administration → Storage** shows configured host/container paths for `DATA_ROOT` and its five folders, their availability and a read-only directory/file browser. Relative host paths are displayed as relative to the Compose file directory; the container does not guess their absolute host location. Managed document versions and RAG indexes remain in PostgreSQL. Generated exports are indexed through the `documents` collection (path `generated-documents` by default, which skips `v<n>/` folders): **Refresh** it in Admin → RAG → Collections after exports change.

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

Change ADMIN_PORT, KEYCLOAK_PORT, PHOENIX_PORT and POSTGRES_PORT in root `.env` if a
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

The admin API owns its configuration (`knowledge_admin/config.py`) and uses the
RAG service. New PostgreSQL management workflows belong in `services/admin-api`.

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

Docs → Generate documents now runs a Claude Code agent; the one-shot generation and its worker are gone. After pulling this version on an existing installation:

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
   `agent_sessions_cc` (`runtime`, `agent_config`, `history`); existing sessions are Claude Code sessions.
2. Start the new service:
   `docker compose --env-file .env -f deploy/compose.yaml up -d agent-session-worker`

Rolling 0074 back refuses while native sessions exist: delete them first.
