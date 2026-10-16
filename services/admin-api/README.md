# Administration API

Independent FastAPI application for Jira settings/tickets, services/server inventory, code projects, RAG workspace management,
agents and agent run records. All `/api` routes require the forwarded `docs-admins` group. Only the
admin OAuth gateway may expose this service; `/health` is its internal health check.

Startup configuration lives in `knowledge_admin/config.py` (`DOCS_WIKI_RAG_URL`,
`DOCS_WIKI_ADMIN_GROUP`) and Phoenix tracing in `knowledge_admin/sessions/tracing.py`
(`DOCS_WIKI_PHOENIX_*`); the variable names are unchanged. Docker binds no API host port.

```sh
uv sync --frozen
uv run --frozen pytest -q
uv run --frozen ruff format   # settings in the repository root ruff.toml (shared with rag and packages/)
# Native development; set the PostgreSQL and issuer variables described below first.
uv run --frozen knowledge-admin "$DATA_ROOT/docs" --env-file ../../.env
```

Use `make up` from the repository root for the authenticated local stack. The
admin web app is available on http://localhost:8183. Future document,
server access and job management belong in this application.

Jira Data Center settings, connection tests and recent-ticket fetching use
`/api/{app}/jira/settings`, `/jira/test`, `/jira/tickets` and `/jira/tickets/fetch`.
The PAT is encrypted; fetches paginate and atomically update the saved selection.
See [Jira API and mock guide](../../docs/jira.md) for request shapes and isolation.

Service routes are `GET/POST /api/{app}/services`, `PUT /api/{app}/services/{id}`,
and `GET /api/{app}/services/{id}/context`. Updates require `revision` from the
last read. The service asset projection joins source repositories and deployments
without credentials. Context is operator-maintained data for future agent consumers;
these routes do not run monitoring or invoke the existing code agent.

`GET/POST /api/{app}/servers` lists server metadata or registers name, hostname/IP,
description, and tags with SSH access and sudo disabled. `PUT /api/{app}/servers/{id}`
updates inventory fields with a required current `revision`; stale edits return 409.
Tags are trimmed, lowercased and deduplicated, limited to 32 tags of 64 characters
each. Server saves preserve deployment links and access configuration, and record
an attributed audit event. `ka.server_assets` supplies the public fields to both the
inventory API and nested service context. Full connection settings are pending.

Code projects use `GET/POST /api/code-projects`, `PUT /api/code-projects/{id}`
and `POST /api/code-projects/{id}/pull`. Folder browsing uses `/api/code/files`
and `/api/code/file`. Historical `/api/repositories` routes remain available.
Mutations require the authenticated subject and admin group. Projects group one
or more folders inside `CODE_ROOT`, optionally with a Git URL and clone path.
Updates require a revision. Credentials are write-only and automatic pull
intervals are stored with the project. See the root README for the UI workflow.

For native repository development set `PGHOST=127.0.0.1`, `PGPORT=5438`,
`PGDATABASE=knowledge_agent`, `PGUSER=knowledge_agent`, `PGPASSWORD` to the local
PostgreSQL password, and `AUTH_ISSUER_URL=http://localhost:8181/realms/docs`.
The `.env` supplies `CREDENTIAL_ENCRYPTION_KEY`; keep it identical to the worker's
key. Run `knowledge-repo-worker` with those same variables plus `CODE_ROOT` pointing
to the managed code directory. Compose wires these settings automatically.

Tests use a disposable PostgreSQL Docker container with its own temporary databases
and real Git checkouts against a local fixture. Existing development data is not used.

## Loading an agent configuration

```python
from knowledge_admin.agents.runner import get_agent_config

config = get_agent_config('operations')  # Saved ID; custom agents use their UUID text ID.
# PostgreSQL connection settings come from PG* variables.
# An explicit connection string can be supplied with conninfo=... .
```

`agents/runner.py` reads a consistent database snapshot and returns a JSON-compatible
dictionary. Its sections are `model`, `system_prompt` (the active version and its
content), `instructions`, `documents`, `code`, `ssh`, `skills`, `mcp`, and `services`.
Identity, description, when-to-use text, availability and revisions are included.
No records are created or updated by loading. A missing agent raises `LookupError`.

It also returns the agent's `access` object as stored (resource references and roles).
Each section holds the items the access object references and is enabled when it has
items; deleted collections, archived code projects and other references to resources
that no longer exist are skipped. Agents are granted whole RAG collections; there are
no subfolder grants.

`code` contains `enabled`, `all_projects`, and `projects`. Each code project has
`id`, `name`, `description`, and absolute `folder_paths` under `CODE_ROOT`. Git-backed
projects additionally include `remote_git_url`; local projects omit that key.
Clone paths, sync state, commits and selection markers are administration metadata
and are not included in these project entries.
`documents.collections` lists each granted collection as `{id, name, description}`.
The document tools search and read by collection name and collection-relative path
(`search_documents`, `read_document(collection, path)`), confined to granted
collections. Agents that are not grounded get the collection folders as read roots.
Both root environment variables must be absolute paths in the agent container.
Compose mounts `DATA_ROOT` read-only at `/work/data` in `admin-api`, with the code,
code-index and generated-documents folders writable. The config joins the stored
relative paths to these roots without resolving paths on the host. `DATA_ROOT` and the
`*_FOLDER` variables are host-side mount sources and never enter the generated paths.
Service repository checkout paths use the same code root; remote deployment log
paths remain paths on their SSH servers. Database paths remain relative.
The configuration holds what the access object references; a run given a service, server or environment also gets
its direct children (its code projects and servers, its deployed services, its services and servers).

Each selected SSH server contains its hostname, port, login user, sudo permission,
trusted host keys and connection settings: always the server's own (there are
no per-agent overrides). Server
availability and its SSH-enabled flag still apply.

Credentials are references, never plaintext or ciphertext in the returned object:

- SSH: `ssh.servers[].private_key_ref` identifies `ka.credentials.ciphertext`.
- Model: `model.api_key_ref` identifies `ka.llm_models.api_key_ciphertext`.
- MCP: `mcp.servers[].bearer_token_ref` identifies `ka.mcp_servers.token_ciphertext`.

Each reference provides the record ID, column, encryption-key environment variable
and exact AAD. There is no stored private-key file. All three use AES-256-GCM:
decode `CREDENTIAL_ENCRYPTION_KEY` from URL-safe Base64, read the referenced bytes,
then decrypt with the first 12 bytes as nonce, the remaining bytes as ciphertext
and tag, and `reference['aad'].encode()` as AAD. Existing backend SSH access uses
`OperationsStore(store, app_name, agent_id).agent_connection(conn, server_id)` to
obtain an `SSHConnection` with the decrypted key in memory. Loading this config
does not need the encryption key. Never send decrypted credentials to an LLM.
The runner supplies instructions and enabled resource context; the tuning reviewer
receives the full configuration with credential references only.

This is the application configuration accepted by `agent()` in the same module.
It is **not** a dictionary to unpack directly into the native Agents SDK:
the function maps enabled sections to provider settings, instructions, MCP
connections and function tools. The Test tab calls
`POST /api/sessions` with `agent_id` and `message` (it may only narrow the agent: experts, subagents, a
subset of its resources, which bring their direct children), which pins this saved configuration (credential references only) in a Test session
(`ka.agent_sessions`, `purpose = 'test'`) that `agent-session-worker` runs; the Test tab then uses the other session
routes under `/api/sessions`. Runs and safe
activity are retained in PostgreSQL; full SDK traces go to Phoenix.

The first message creates a session. Follow-ups continue its Agents SDK conversation
using the session’s pinned configuration. New sessions pin the agent’s saved configuration
when they start. Sessions close explicitly or are closed
after 24 hours without activity; a session with a running turn is never closed for inactivity. Session
history and tuning reviews remain in PostgreSQL after runtime files are removed.
See [session and review endpoints](../../docs/agents.md) for the API contract.
The tuning reviewer receives full configuration, the whole stored conversation,
and administrator feedback. It proposes changes without applying them.

The agent's **Test → Settings → Preview** shows the configuration a run would pin
(`POST /api/sessions/preview`). Previewing does not create a run or send the task to a model.
`GET /api/agents/{agent_id}/config` returns the saved configuration; it is admin-only with caching disabled.

## Running a configured agent with the Agents SDK

Test conversations and native Generate documents sessions call the same function. It accepts the saved configuration and separately resolved credentials;
it never queries PostgreSQL.

```python
from knowledge_admin.agents.runner import agent, get_agent_config

config = get_agent_config('code')
result = await agent(config, 'Explain how this project starts.', credentials=decrypted_credentials)
print(result.final_output)
```

Setup is split into `get_model`, `get_ssh` and `get_prompt` in
`agents/runner.py`; tools come from `tools/catalog.py` through `tools/sdk.py` (MCP server selection is in
`knowledge_admin/capabilities/mcp.py`). These select the saved settings and enabled
resources. Provider connections still belong to `model_adapter.agent_model()`;
the main runner owns connection cleanup and streaming. The session API owns
conversation persistence.

`agent()` creates `agents.Agent` and executes `Runner.run_streamed()`, returning
its native `RunResultStreaming`. Failures, cancellation, timeout and exhausted
turn limits raise exceptions. Token usage is in `result.context_wrapper.usage`.
`timeout_seconds` defaults to 300 and `max_turns` to 20. For structured results,
pass an existing Pydantic class as `output_type`; `final_output` is that model.
The agent wizard and tuning reviewer use this support.

Every run gets every catalog tool (no per-agent choice; migration 0115 dropped `ka.agent_tools`); what a tool may do
is decided by its check against the run's access object. Tool implementations are in `knowledge_admin/capabilities/`
(`files`, `ast_grep`, `code_search`, `code_graph`, `documents`, `ssh`, `mcp`, `shell/`); `tools/catalog.py` is the
SDK-free catalog and `tools/sdk.py` the single Agents SDK adapter. The MCP servers in a run's access offer every one of
their tools. `code_graph` answers when a given code project has a current generated graph (`current_graph`, read live
from the manifest; a sync after generation adds a note).
Enabled code/document folders offer `list_files`, `read_file` and
`search_files` from `knowledge_admin/capabilities/files.py` (wrapped by
`knowledge_admin/tools/catalog.py`). These read configured
absolute mounted paths. SSH registers `ssh_run`, which uses saved connection
settings and credentials in backend context, enforces selected servers and sudo
settings, and reuses the AsyncSSH transport. Keys are not written to disk or
included in prompts. The arbitrary local `shell_run` tool is not registered in
the administration process; it requires a separate isolated execution worker.

The saved active prompt, agent instructions, enabled resources, and enabled
skills form the system instructions. Selected server instructions/skills remain
part of each server's context. Disabled capabilities are omitted. Selected HTTP
MCP servers connect using `MCPServerStreamableHttp`, with private bearer headers
and server-qualified tool names to avoid collisions.

`model_adapter.agent_model()` selects `OpenAIResponsesModel`,
`OpenAIChatCompletionsModel`, or `LitellmModel` for Anthropic/Bedrock. There is no
intermediate HTTP proxy or Codex dependency. Bedrock uses the saved bearer key,
region and model ID. Credentials are specific to each invocation, and native
providers require a saved key instead of using environment credentials.

Test sessions and Generate documents sessions of native agents are run by `agent-session-worker` through
`HistorySession` (below); the SDK items of successful turns are saved in `ka.agent_sessions.history`, `purpose`
distinguishes the two, and sessions close after 24 hours without activity. Tool workspaces are temporary; the runner
does not read or write session-history files. See [session APIs and migration](../../docs/agents.md).

Generate documents sessions of native agents pass `session=HistorySession` instead (`knowledge_agents/history.py`, an Agents
SDK `Session`) with `stream=False` and `hooks=SessionEvents`; `agent()` refuses a session together with `history` or
streaming. Their turns pass `max_turns=None, turn_timeout_seconds=None` (no turn limit; `timeout_seconds` still bounds each
model request and MCP connection).

## Live activity and Phoenix traces

`agent(config, task, credentials=..., on_activity=callback)` consumes Agents SDK
stream events and tool lifecycle hooks. Public messages and tool input/output
are sent to the existing UI callback. Reasoning and raw provider errors are
excluded. Credentials are redacted before activity or span export, including
secret prefixes split across streamed message chunks.

Phoenix stores the execution history. The SDK creates agent, model, and tool spans; OpenInference exports them to
Phoenix. `sessions/tracing.py` adds credential/error filtering before export and disables OpenAI's default trace
exporter. `SdkActivity` only publishes UI previews. Inputs, outputs, token counts, errors, and timings are recorded
with bounded text sizes. Traces go to the Phoenix project `docs-wiki` (name kept from earlier versions) using the
`DOCS_WIKI_PHOENIX_*` OTLP configuration. Phoenix handles its own login.

Full configuration snapshot lifecycle and API: [agent versions](../../docs/agents.md#configuration-versions).
