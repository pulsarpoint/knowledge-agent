# Standalone v3 deployment

Run all commands from the v3 repository root. `make up` prepares root `.env`,
builds local images and starts `deploy/compose.yaml`. The Compose project name is
`knowledge-agent-v3`. Do not copy a v2 `.env` or reuse its data volumes.

## Service boundaries

- Reader traffic enters through `oauth2-proxy` on port 8180; administration enters
  through `admin-oauth2-proxy` on port 8183. The two web servers and APIs, `rag`
  and `litellm` have no published host ports.
- The proxies use separate `knowledge-agent-v3` and `knowledge-agent-v3-admin`
  Keycloak clients, client secrets and cookie names. The admin gateway also
  requires the `docs-admins` group, which the admin API checks independently.
  Browser login uses the public URL, while token/userinfo/JWKS calls use Docker
  service DNS. Issuer validation remains enabled. This follows the documented
  [explicit OIDC endpoint configuration](https://oauth2-proxy.github.io/oauth2-proxy/configuration/overview/)
  and Keycloak's [backchannel hostname configuration](https://www.keycloak.org/server/hostname).
- `migrate` runs the pinned golang-migrate v4.19.1 CLI after PostgreSQL becomes
  healthy and records state in `public.golang_schema_migrations`. Application
  startup requires successful migration. Existing checksum-ledger databases need
  the one-time verified adoption procedure in the [migration guide](../docs/migrations.md).
  Repository settings, encrypted
  tokens and pull jobs use PostgreSQL; SQLite remains the reader's current store.
- `repository-worker` has no host port and owns the writable `/work/code` bind
  mount. It claims PostgreSQL jobs, clones and periodically pulls Git source into named folders. The admin API
  and source browser mount the same folder read-only.
- `jira-mock` is an optional local test profile. `make jira-mock-up` starts 75
  synthetic tickets on an internal Docker network shared with `admin-api`, with
  no host port or external connection. `make jira-mock-down` stops it. See the
  [Jira mock guide](../docs/jira.md) for test credentials and failure scenarios.
- Phoenix has its own authenticated UI and volume. The reader exports
  OpenInference traces to it; tracing failure does not prevent reading or chat.
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
created inside the checkout. Paths must be absolute.

| Folder | Override | Container path | Contents |
| --- | --- | --- | --- |
| `DATA_ROOT/code` | `CODE_PATH` | `/work/code` | Code projects, Git clones and caches |
| `DATA_ROOT/code-index` | `CODE_INDEX_PATH` | `/work/code-index` | Code graph snapshots |
| `DATA_ROOT/rag-data` | `RG_DATA` | `/work/rag-data` | Writable folder for Jira exports (`rag-data/jira`); RAG also indexes the other areas read-only |
| `DATA_ROOT/generated-documents` | `GENERATED_DOCS_PATH` | `/work/generated-documents` | Current Markdown/D2 export of each document: `<slug>/` or `services/<svc>/<phase>/`; indexed by the `documents` collection (old `v<n>/` folders are excluded) |
| `DATA_ROOT/docs` | `DOCS_PATH` | `/work/docs` | Bootstrap docs seeded from `examples/docs` |

RAG collections can hold paths in any of these five folders. The `rag` and `rag-worker`
services mount all of them at the container paths above, read-only except `rag-data`.

Set an override in `.env` (absolute path) only to put one folder elsewhere, for
example code on a larger disk. Admin → Storage shows the host and container path
of each folder.

**Upgrading to RAG collections (migration `0063`):** after `make up`, admin-api
re-exports every document whose current export is missing into `generated-documents`
(background, idempotent, failures only logged). Every collection then shows **Needs
refresh** in Admin → RAG → Collections; **Refresh** each one (or **Refresh all stale**).
Expect a full re-parse and, with embeddings on, a full re-embed (for example about 975
chunks for `jira`). Until a collection is refreshed its previous index keeps serving search.

**Moving the data:** `make down`, copy the folders (for example
`rsync -a .local/ /opt/knowledge-agent/`), set `DATA_ROOT` in `.env`, remove any
overrides that pointed at the old place, then `make up`.

## Data and lifecycle

| Location | Contents |
| --- | --- |
| `DATA_ROOT` host folder | Code, code graphs, RAG sources, document exports and bootstrap docs (see above) |
| `knowledge-agent-v3_postgres-data` | V3 application metadata, processing jobs, searchable text and vectors |
| `knowledge-agent-v3_wiki-data` | Reader sessions, audit transcript and gaps |
| `knowledge-agent-v3_keycloak-data` | Independent realm, users and login configuration |
| `knowledge-agent-v3_phoenix-data` | Independent Phoenix traces and accounts |

**Administration → Storage** shows configured host/container paths for these four content mounts, their availability and a read-only directory/file browser. Relative host paths are displayed as relative to the Compose file directory; the container does not guess their absolute host location. Managed document versions and RAG indexes remain in PostgreSQL. Generated exports are indexed through the `documents` collection (path `generated-documents`, which skips `v<n>/` folders): **Refresh** it in Admin → RAG → Collections after exports change.

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

`make admin-user` creates `local-admin` in the running `docs` realm. Set
`ADMIN_USERNAME` and optionally `ADMIN_EMAIL` to add another account. It uses the
[Keycloak Admin REST API](https://www.keycloak.org/docs-api/latest/rest-api/index.html)
and the bootstrap administrator credential from `.env`; it only accepts the v3
Compose project and a loopback Keycloak origin. This creates an application
administrator through the `docs-admins` group, not a Keycloak server administrator.

Passwords are generated and stored in `.local/users/<username>.json` with mode
0600. Repeating the command retains existing profiles and passwords. To rotate
one intentionally, run:

```sh
python3 scripts/admin_user.py --username <username> --reset-password
```

The script prints a private recovery-file path before each
password write; if the network fails, retain that file because Keycloak may have
accepted the write. A successful write moves it to the regular credentials path.

Check an account with:

```sh
python3 scripts/smoke.py --credentials .local/users/<username>.json --auth-only
```

The default `make smoke` still uses
the bootstrap application admin. Disabling/removing accounts, changing groups,
or other user administration is available through the local Keycloak console.

## Configuration and troubleshooting

Change WEB_PORT, ADMIN_PORT, KEYCLOAK_PORT, PHOENIX_PORT and POSTGRES_PORT in root `.env` if a
port is occupied. Set public WEB_HOST consistently; for an already imported
realm, `make up` applies the client redirect URIs to match these settings.

```sh
make compose-validate
make status
docker compose --env-file .env -f deploy/compose.yaml logs --tail=80 keycloak oauth2-proxy
```

The smoke check uses the bootstrap application admin and intentionally rebuilds
the configured v3 docs/code indexes. If its password has changed in Keycloak,
update APP_ADMIN_PASSWORD to the current credential before running the check.
Smoke does not issue an AI prompt or verify provider credentials. The example
corpus is expected to contain readable Markdown and extractable Go code.

For short private hostnames used by SSH or Jira, set `DOCKER_DNS_SEARCH` in root `.env` to the host's DNS search domain (for Tailscale, the tailnet's `tailXXXX.ts.net` domain). Compose applies this to the admin API, where those outbound connections run. Recreate the container with `docker compose --env-file .env -f deploy/compose.yaml up -d --no-deps --wait admin-api`. Docker retains its normal DNS resolver and internal service discovery. With no domain configured, the search suffix is `.`.

This fixes short-name lookup when the full hostname already resolves inside Docker. It does not copy the host's `/etc/hosts`, SSH aliases, or VPN routing configuration. If the full name also fails, fix the Docker/host DNS forwarding or use a reachable DNS server for that domain. See [Compose dns_search](https://docs.docker.com/reference/compose-file/services/#dns_search) and [Tailscale MagicDNS](https://tailscale.com/docs/features/magicdns).

To run native services, use the service guides' `uv run` commands. The frontend's
reader Vite proxy expects `127.0.0.1:8765`; the admin Vite proxy on port 5174
expects its own API on `127.0.0.1:8766`. Authenticated Docker entry points use
8180 and 8183 respectively. Do not expose the native internal APIs as a
substitute for the authenticated gateway.

## Deploy and maintain the applications separately

```sh
# Rebuild just the admin frontend/API after their source changes.
docker compose --env-file .env -f deploy/compose.yaml up -d --build admin-api admin-web repository-worker admin-oauth2-proxy
# Stop administration; the reader continues serving its users.
docker compose --env-file .env -f deploy/compose.yaml stop admin-oauth2-proxy admin-web admin-api repository-worker
```

The `keycloak-configure` one-shot service applies only the two v3 client
definitions after Keycloak becomes healthy. It preserves existing users and
application data. Admin group claims come from the dedicated client mapper;
the gateway explicitly requests `openid email profile` to avoid requesting a
nonexistent `groups` scope. Session encryption keys differ across applications.

The admin API reuses the reader package's configuration and audit-storage modules
as a library. It does not call the reader API or mount its routes. Both services
currently share `wiki-data` to support cross-user audit inspection, and both use
the RAG service. New PostgreSQL management workflows belong in `services/admin-api`.
