# Knowledge Agent v3

An independently runnable successor to v2, with its own source, locked
dependencies, Docker images, authentication, tracing, and persistent data.

The application currently provides configuration administration through FastAPI
and React/shadcn. Agents, prompt versions, skills, model assignments, SSH settings,
and resource selections are stored in PostgreSQL. Keycloak provides login and
Phoenix remains available for tracing.

Git sync, Jira ticket import/export, RAG file processing/search, source browsing,
and connection tests work. Agent Test (Admin → Agents) runs models; agents can be set to answer only from documents with citations.
See [agent administration](docs/agent-profiles.md) and [execution routes](docs/agents.md).

**New here? Start with the [User Guide](docs/user-guide.md)** — how to use the
Administration application end to end, verified against the current code.

## Start locally

Requires Docker with Compose, and Python 3.10+ for the setup script. No v2 checkout,
Node, Go, local Python packages, or model credentials are needed to start:

```sh
make up
make smoke
```

The first run needs the host folder for the application's files:
`make setup DATA_ROOT=/opt/knowledge-agent` (see [data folders](deploy/README.md#data-folders)).
`make up` generates local secrets in `.env` (mode 0600), seeds `DATA_ROOT/docs` only
when empty, builds the application images from this checkout, migrates
PostgreSQL, and waits for service health. Repeating setup preserves credentials
and documentation. `make smoke` checks the admin Keycloak login and gateway isolation, then checks
RAG settings, collections and search, database migrations and Phoenix. It does not invoke a model.

| Service | Local address | Login |
| --- | --- | --- |
| Administration app | http://localhost:8183 | `v3-admin`, password from `APP_ADMIN_PASSWORD` in `.env` |
| Keycloak administration | http://localhost:8181 | `admin`, password from `KEYCLOAK_ADMIN_PASSWORD` |
| Phoenix | http://localhost:8182 | `admin@localhost`, password from `PHOENIX_ADMIN_PASSWORD` |
| PostgreSQL | `127.0.0.1:5438` | database/user `knowledge_agent`, password from `POSTGRES_PASSWORD` |

The defaults bind only to localhost. Runtime state lives in Compose volumes
prefixed `knowledge-agent-v3_`; files live in the `DATA_ROOT` host folder. V2's data, credentials,
volumes and ports are not reused. Keycloak realm import creates the application
admin on the first boot; change its password in Keycloak afterward, not by
editing the bootstrap value in `.env`.

```sh
make status        # service status
make logs          # follow service logs
make down          # stop; preserve all data volumes
```

## Administration application

Administration (`services/admin-ui` + `services/admin-api`) runs on port 8183 behind
its own oauth2-proxy gateway and Keycloak client. The admin API serves configuration,
integration actions and agent runs. Admin access requires `docs-admins` at both
the gateway and the API.

Any member of the `docs-admins` group can sign in to the admin application.
`make up` configures the OIDC client for fresh and existing environments without
reimporting users. `ADMIN_PORT` controls the administration port.

The end-user Reader application (port 8180) was removed and will be redesigned
later; agent **Test** in Admin → Agents is the way to try agents. When upgrading an
existing installation, follow [Upgrading after the Reader removal](deploy/README.md#upgrading-after-the-reader-removal).

## Administrators

Sign in to the administration application as `v3-admin` with the password `APP_ADMIN_PASSWORD` from
`.env`. To add another administrator, create the user in the Keycloak console
(http://localhost:8181, realm `docs`) and add it to the `docs-admins` group; the user
must sign out and in again to pick up the group.

```sh
python3 scripts/smoke.py --auth-only
```

The auth-only check verifies real SSO and protected admin access. `make smoke`
checks the RAG collections workspace, full-text search, PostgreSQL and Phoenix without
processing data or calling a model. Admin → RAG shows collections (each one or more
paths under `DATA_ROOT`, with its own search and processing settings), refresh, the global
embedding model and job history. Activity shows usage/audit data; Code
projects manages local folder mappings and Git pulls. Services and Servers record
deployed applications and their hosts. Configure AI connections in LLM Settings.

## Services and agent assets

Open [Administration → Services](http://localhost:8183/services) to add an
application, its purpose, owner/team, and behavior/investigation notes. Select
any number of saved code projects; the same code project can serve multiple
applications. Each service can have multiple deployment records with a server,
environment, runtime/manager, container or unit name, application port, log
location, and reported deployed version. Unknown source/deployment links can be
filled in later. Filter the list by service, repository, server or environment.

Open [Administration → Servers](http://localhost:8183/servers) to register and
edit hosts with a name, IP/hostname, description, and tags. Tags are comma-separated
in the form, normalized to lowercase, and deduplicated (up to 32 tags, 64 characters
each). Search by name, address, description, or tag. The **Register server** action
inside a service uses the same form and inventory.

Server edits keep existing deployment links and appear in service details and
the service context API immediately. Saves check revisions to protect concurrent edits.
These inventory records start with remote access disabled, no SSH username
or credentials, and sudo disabled. Registering a server does not connect to it.
SSH settings support saved credentials and connection tests; agents can store per-server overrides.

**Details → Agent context (JSON)** exposes a scoped, authenticated read-only
resource at `/api/services/{id}/context`. It includes service notes,
repository URLs/downloaded commits/checkout paths, and deployments/server metadata.
It excludes connection credentials. The admin UI and context API share one
PostgreSQL projection, so later repository pulls appear in the context immediately.
The downloaded source commit is never presented as the deployed version.

This is operator-maintained inventory, not observed health. Agent monitoring,
SSH/log collection, and automatically passing these assets into agent runs are
not connected yet. Future consumers must treat all descriptions and paths as
untrusted contextual data, and enforce server access policy before any operation.
Service saves are atomic and revision-checked to avoid overwriting another admin's
changes; configuration edits are attributed in the audit log.

## Jira

[Jira Settings](http://localhost:8183/jira/settings) stores your local Jira
server URL, username and encrypted personal access token, with **Test connection**.
[Jira Tickets](http://localhost:8183/jira/tickets) fetches the most recently
updated tickets with a selectable limit of 1–200 and saves the results locally.

For isolated testing, run `make jira-mock-up` and configure URL
`http://jira-mock:8080/jira`, user `mock-admin`, token `jira-mock-token`. This optional
Docker mock supplies 75 sample tickets and authentication/permission/rate-limit
responses without contacting a real Jira server. Stop it with `make jira-mock-down`.
See [Jira setup, mock scenarios, and API behavior](docs/jira.md).

## Code projects

Open [Code → Projects](http://localhost:8183/code-projects); generated graphs are listed under [Code → Code graphs](http://localhost:8183/code-graphs).
The folder browser shows the mounted code root, including nested folders and text
file previews. **Add project** groups one or more existing folders under a name
and description. The form contains an expandable folder tree with checkboxes:
select any existing folder at any depth, including folders from different branches.
Selections stay checked when branches collapse; editing expands the ancestors of
saved selections. Folders load on expansion, with pagination for large directories.
All selected folders must already be inside the mount. Editing mappings never
deletes the underlying files.

Choose **Git repository** to add an HTTPS Git source. Enter a clone folder name
relative to the code root (prefilled from the project name), an optional
branch/tag/commit and access token. Source files live directly in that folder;
new projects do not add workspace/repository UUID directories.
**Clone repository after saving** controls the initial clone; leave it unchecked
to save configuration and use **Clone** later. **Automatic pulls** schedules pulls
at the selected interval (5 minutes to 7 days), or leave it off for manual pulls.
A Git project can include additional existing folders too.

Code lives in `DATA_ROOT/code` on the host (the folder is `CODE_FOLDER`).
Compose mounts `DATA_ROOT` read-only at `/work/data`; the browser displays
both paths. The repository worker (and admin API) have the code folder writable and
write clones. Arbitrary host paths and symbolic links cannot be mapped.

New clones keep the chosen folder across pulls. The worker fetches shallow
history and checks out the requested commit, without running source code, hooks,
submodules or Git LFS downloads. Pulls stop if the clone has local modifications,
untracked/ignored files, an altered origin or an unexpected HEAD. Existing
projects keep their IDs, links and downloaded folders; older snapshot-style
projects continue working. Editing one adopts its current checkout as its fixed
clone folder. Old snapshots and Git caches are retained.

Public repositories need no token. Private GitHub sources use a personal access
token with repository read access. Tokens are encrypted with AES-GCM in PostgreSQL
and never returned to the browser. A blank token field preserves the saved token;
**Remove saved token** explicitly clears it. Keep `CREDENTIAL_ENCRYPTION_KEY` with
your recovery materials, separately from database backups. Git authentication
uses a transient askpass process, without credentials in remote URLs.

The existing repository worker handles both queued and scheduled pulls. Duplicate
pull requests reuse the active job. Each pull has a ten-minute execution limit
and an eleven-minute job lease. The page displays progress and failures. Code
projects are available in agent resource selections and service configuration;
agent configuration previews include all mapped folders.

## Content and model configuration

RAG indexes **collections**: each holds one or more folders in `DATA_ROOT` (for example `code/payments`,
`jira`, `generated-documents` or `docs/runbooks`) and is indexed where it is, with no copy step.
Administrators create collections and connect folders in RAG → Collections; sources only write files. Auto-sync checks folders at an interval.
The RAG services mount `DATA_ROOT` read-only at `/work/data` and write only the Jira
folder (`JIRA_FOLDER`). **Refresh** collections when files change, and review jobs through RAG
administration. Unchanged files are identified by content hash so processing can be reused. Add and pull Git repositories
through Code projects.

LLM Settings stores named OpenAI-compatible, Anthropic and Bedrock connections
with encrypted credentials. Model connection tests still contact the configured
provider. Agent pages store model selections, prompts and capabilities for future
execution. Agent Test requires a model assignment.


## Development and tests

For native development install Python 3.12+, uv, Node 22.12+, and pnpm 9.12.

```sh
make build          # sync Python dependencies and build the UI
make test           # setup, rag and admin-api, the admin UI and PostgreSQL tests
make compose-validate
```

Dependency resolution uses copied lockfiles and frozen installs. Internal
package names (for example `rag`) remain stable so their tested
imports and CLI entry points stay intact.

Database-only commands remain available: `make db-up`, `make db-migrate`,
`make db-test`. The pinned golang-migrate container reads PostgreSQL connection
settings from `PG*` variables. Existing databases managed by the former checksum
runner require the verified one-time `make db-adopt` step described in the
[migration guide](docs/migrations.md).

## Design and provenance

- [Architecture and planned admin workflows](docs/architecture.md)
- [Database model and diagram](docs/database.md)
- [PostgreSQL migration and adoption guide](docs/migrations.md)
- [Port inventory and remaining migration work](docs/v2-migration.md)
- [Standalone deployment details](deploy/README.md)
- [Runtime verification results](docs/runtime-verification.md)
- [Preserved app-docs plugin](plugins/README.md)

The plugin is bundled locally and can be loaded directly by the Claude Agent
SDK. For manual Claude use, register this checkout as a marketplace and install
`app-docs@knowledge-agent-v3`. Its automated admin integration is not yet wired.

### RAG collections administration

The separate admin app includes RAG Collections, Processing jobs, Settings, and Search. A collection is a folder inside `DATA_ROOT` with its own search mode, embeddings, include/exclude patterns and chunking; agents are granted whole collections. See [the RAG guide](docs/rag.md) for collections, freshness and Refresh, incremental processing, `/search` across collections, credentials, and API contracts.

Administration → Agents manages configuration and offers direct agent Test conversations. See [Agents](docs/agents.md).

Administration → Skills manages reusable Markdown instructions with descriptions, enable/disable controls, and revision-checked edits. See [Skill library](docs/skills.md).

## Agent system prompts

[Agents](http://localhost:8183/agents) provides a Prompts tab for each agent, with draft and active versions. Search diagrams are
optional and checked with Mermaid before rendering. See [the prompts guide](docs/prompts.md).
