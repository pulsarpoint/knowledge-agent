# Agent tools and resources

Status: design approved 2026-10-04 (simplified from the earlier "tool grants" draft); awaiting
spec review.

## Goal

An agent is **instructions + skills + tools + resources**:

- **Tools** — what the agent can do, picked from one catalog where built-in tools and MCP
  servers look the same. Each tool has editable **usage docs** that are added to the agent's
  instructions only when the tool is picked.
- **Resources** — what all of the agent's tools work within: servers, services, document
  folders and code projects. A selected service also brings its deployments' servers and its
  code projects.
- **Skills** — reusable instruction blocks (unchanged).

New kinds of agents (Docs agent, Server agent, Log agent for lower-privileged users,
documentation agent) are made by picking tools and resources, not by adding flags.

Success criteria:

- After migration, existing agents can do what they did before (with the documented
  Documents change).
- An agent given only **SSH logs** can read logs on its effective servers and has no way to run
  any other command, even if prompted to.
- The model sees usage docs and functions only for tools that are picked.
- Every SSH call is recorded with the real user behind it.
- A documentation agent that selects only a service can reach that service's deployment
  servers and code projects.

## Context and roadmap

Spec 1 of 4: **agent tools and resources** (this spec) → server profiles
(`2026-10-04-server-profiles-design.md`, migration 0062) → Reader Support tier and group
gating → runbooks. It replaces the capability switches (`documents_enabled`,
`repositories_enabled`, `ssh_enabled`, `mcp_enabled`, `services_enabled`) and the
`documents_grounded` flag.

## Agreed decisions

| Decision | Choice |
| --- | --- |
| Agent model | Instructions + skills + tools + resources; no per-tool scopes, policies or capability switches. |
| Restricting access | Different tools, not policies: SSH comes as *SSH logs*, *SSH diagnostics* and *SSH commands*. An agent can only use the functions of the tools it has. |
| Tool docs | Every tool has editable usage docs, included only when picked. |
| MCP | MCP servers are tools in the same catalog. Built-in tools stay native functions internally (they need the real user, allowed servers, keys, sandbox and citation state); publishing them as an MCP server is out of scope. |
| Resources | Servers, services, document folders, code projects, chosen once per agent; services expand to their deployments' servers and their code projects. |

## Tool catalog

Built-in tools (seeded) and one tool per MCP server:

| Tool id | Name | Functions given to the model | Resource used |
| --- | --- | --- | --- |
| `documents_search` | Documents search | `search_documents`, `read_document` | document folders |
| `documents_report_gaps` | Report documentation gaps | `report_not_covered` | — (works with Documents search) |
| `code_reading` | Code reading | `list_files`, `read_file`, `search_files`, `code_search`, `ast_grep`, `code_graph` (when graphs exist) | code projects |
| `shell_sandbox` | Shell sandbox | `shell_run` (no network; code projects read-only; scratch writable) | code projects |
| `ssh_logs` | SSH logs | `service_logs`, `container_logs`, `tail_log` | servers |
| `ssh_diagnostics` | SSH diagnostics | the SSH logs functions + `service_status`, `container_list`, `disk_usage`, `memory`, `listening_ports`, `http_check` | servers |
| `ssh_commands` | SSH commands | `run_command` (free text; sudo only when the server allows it) | servers |
| `service_lookup` | Service lookup | `lookup_service` (details, deployments, dependencies) | services |
| `mcp:<mcp_server_id>` | the MCP server's name | the server's discovered tools | — |

Seeded usage docs:

- `documents_search`: how to search and read excerpts.
- `documents_report_gaps`: the rules formerly in `grounding.POLICY` — answer only from returned
  excerpts, cite `[n]`, never cite numbers a tool did not return, call `report_not_covered`
  and say what is missing when the excerpts do not answer.
- `code_reading`, `shell_sandbox`, `ssh_commands`: the guidance currently hard-coded in
  `get_prompt`.
- `ssh_logs`, `ssh_diagnostics`: use the server IDs from resources; follow per-server
  instructions; prefer narrow time ranges.
- `service_lookup`: short usage notes. MCP tools: the MCP server's description until edited.

### SSH functions

Arguments are typed and validated before any connection; admin-api builds the command and
shell-quotes every argument.

| Function | Arguments | Command |
| --- | --- | --- |
| `service_status` | `server_id`, `unit` | `systemctl status <unit> --no-pager` |
| `service_logs` | `server_id`, `unit`, `since` (`<n>m`, `<n>h`, `<n>d`, max 7 days), `lines` 1–2000 | `journalctl -u <unit> --since -<since> -n <lines> --no-pager` |
| `container_list` | `server_id` | `docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}'` |
| `container_logs` | `server_id`, `container`, `since`, `lines` | `docker logs --since <since> --tail <lines> <container>` |
| `tail_log` | `server_id`, `path`, `lines` | `tail -n <lines> <path>` |
| `disk_usage` | `server_id` | `df -hT -x tmpfs -x devtmpfs` |
| `memory` | `server_id` | `free -m` |
| `listening_ports` | `server_id` | `ss -tlnp` |
| `http_check` | `server_id`, `url` | `curl -sS -o /dev/null -w '%{http_code} %{time_total}' --max-time 10 <url>` |
| `run_command` | `server_id`, `command`, `as_root` | today's behaviour |

- `unit` and `container` match `^[A-Za-z0-9@._:-]{1,200}$`.
- `tail_log` paths are absolute, contain no `..`, and lie under `/var/log` or a log location
  recorded for a deployment on that server; the remote command resolves the path with
  `readlink -f` and refuses if it leaves those roots.
- `http_check` URLs use http/https and target the server's own hostname, `localhost` or
  `127.0.0.1`.
- All SSH functions reuse the existing runner (timeout, `setpriv --no-new-privs`, 32 KiB output
  cap). Only `run_command` may use sudo, and only when the server allows it.

## Resources

| Resource | Storage (existing) | Used by |
| --- | --- | --- |
| Servers | `operations_servers`; per-server instructions in `agent_configs`; the agent's own SSH connection override in `agent_server_connections` | SSH tools |
| Services | `agent_services` | Service lookup and service context; expands (below) |
| Document folders | `agent_knowledge_folders`, `agents.all_folders`, `agents.include_subfolders` | Documents search |
| Code projects | `agent_repositories`, `agents.all_repositories` | Code reading, Shell sandbox |

- **Effective servers** = selected servers ∪ servers of the selected services' deployments
  (`service_deployments.server_id`), limited to servers that are enabled and SSH-allowed.
- **Effective code projects** = selected code projects (or all) ∪ the selected services'
  linked code projects (`service_repositories`), excluding archived projects.
- Per-server instructions and connection overrides apply to effective servers the same way
  whether a server was selected directly or came from a service.

## Data model — migration `0061_agent_tools`

```sql
CREATE TABLE ka.tools (
    id            text PRIMARY KEY CHECK (id ~ '^([a-z_]{1,40}|mcp:[0-9a-f-]{36})$'),
    source        text NOT NULL CHECK (source IN ('builtin','mcp')),
    mcp_server_id uuid UNIQUE REFERENCES ka.mcp_servers(id) ON DELETE CASCADE,
    name          text NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
    description   text NOT NULL DEFAULT '' CHECK (length(description) <= 4000),
    usage         text NOT NULL DEFAULT '' CHECK (length(usage) <= 20000),
    revision      integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    updated_by    uuid REFERENCES ka.principals(id),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    CHECK ((source = 'mcp') = (mcp_server_id IS NOT NULL))
);

CREATE TABLE ka.agent_tools (
    agent_id text NOT NULL REFERENCES ka.agents(id) ON DELETE CASCADE,
    tool_id  text NOT NULL REFERENCES ka.tools(id) ON DELETE CASCADE,
    PRIMARY KEY (agent_id, tool_id)
);

CREATE TABLE ka.ssh_actions (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_id   uuid NOT NULL REFERENCES ka.projects(id),
    run_id       uuid,
    session_id   uuid,
    agent_id     text NOT NULL,
    server_id    uuid,
    actor_id     uuid REFERENCES ka.principals(id),
    function     text NOT NULL,
    arguments    jsonb NOT NULL,
    outcome      text NOT NULL CHECK (outcome IN ('succeeded','failed','refused')),
    exit_code    integer,
    output_bytes integer,
    duration_ms  integer,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ssh_actions_recent ON ka.ssh_actions(project_id, created_at DESC);
```

- The migration seeds the eight built-in tools and one `mcp:<id>` tool per existing MCP server.
  Creating an MCP server inserts its tool row; deleting it removes the row (and picks) by
  cascade.
- `ssh_actions` rows are only inserted; `agent_id`/`server_id` are plain values so the audit
  survives deletions.

### Conversion of existing agents

| Today | Tools picked |
| --- | --- |
| `documents_enabled` | `documents_search`, plus `documents_report_gaps` if `documents_grounded` |
| `repositories_enabled` | `code_reading`, `shell_sandbox` |
| `ssh_enabled` | `ssh_commands` (same as today) |
| `mcp_enabled` + agent-level MCP selections (`agent_config_mcp_servers` of the agent's `server_id IS NULL` config) | each selected server's `mcp:<id>` tool |
| `services_enabled` | `service_lookup` |

- Resource selections are kept as they are. A capability that was off simply picks no tool;
  its selections remain as resources.
- The coordinator picks no tools.
- The migration raises an error naming the agents if any per-server skill or MCP override
  exists (`agent_configs.server_id IS NOT NULL AND (NOT inherit_skills OR NOT inherit_mcp_servers)`).
- It then drops the columns `agents.documents_enabled`, `documents_grounded`,
  `repositories_enabled`, `ssh_enabled`, `mcp_enabled`, `services_enabled`,
  `agent_configs.inherit_skills`, `agent_configs.inherit_mcp_servers`, and the tables
  `agent_config_skills` and `agent_config_mcp_servers`. `skills_enabled` and `skills_agents`
  stay.
- The down migration recreates those columns and tables from `agent_tools`
  (`documents_report_gaps` → `documents_grounded`, `ssh_logs`/`ssh_diagnostics` → `ssh_enabled`);
  per-server overrides are not restored.

## Configuration and versions

- `get_agent_config` returns `config['tools']` (picked tools: id, name, usage, functions) and
  `config['resources']` (`servers`, `services`, `folders`, `code_projects`, already expanded
  through services, with per-server instructions and connection data), replacing the old
  `documents`/`code`/`ssh`/`mcp`/`services` blocks.
- Agent version snapshots store both, including the usage text, so a version reproduces what
  the model saw.
- `upgrade_snapshot(config)` converts snapshots saved before 0061 with the conversion table;
  it runs wherever snapshots are loaded (sessions, versions, board).
- Tool usage edits and agent tool/resource edits are revision-checked (409 "…changed. Reload
  before saving.") and recorded in `ka.audit_events`.

## Runtime

- `get_tools(config)` returns the functions of picked tools that have something to work on:
  SSH tools need at least one effective server, `code_reading`/`shell_sandbox` a code project,
  `documents_search` a folder (or all folders), `service_lookup` a service.
- Every function re-checks at call time that its tool is picked and that the requested server,
  folder, path or project is in the agent's effective resources; otherwise it refuses with a
  message to the model.
- `get_prompt(config)` order: system prompt → additional instructions → skills → usage docs of
  each picked tool → resource context (servers with per-server instructions, services, folder
  descriptions, code projects) → the existing secrets/resources rules. Unpicked tools add no
  text.
- Every SSH function call (including refusals) writes one `ssh_actions` row with the run,
  session, agent, server, the run's actor (the real user), redacted arguments, outcome, exit
  code, output size and duration.
- `agents.agent()` and `create_agent()` signatures are unchanged.

### Reader Knowledge tier

`knowledge_violations(config)` becomes tool-based: the agent may only have
`documents_search`, `documents_report_gaps` and `service_lookup`, and must have the first two.
Reasons: "uses SSH", "uses code", "uses the shell", "uses MCP tools", "document search with gap
reporting is off"; the disabled-agent and no-model reasons stay.

## API

| Method and path | Purpose |
| --- | --- |
| `GET /api/tools` | Catalog: tools, functions, usage, revisions |
| `PUT /api/tools/{id}` | Edit usage docs (and description) `{usage, description, revision}` |
| `PUT /api/agents/{id}` | Gains `tool_ids: string[]`; loses the capability fields |
| `GET /api/agents/{id}/resources` | Selected and effective resources (with what each service brings in) |
| `GET /api/ssh-actions` | Audit list, filters `server_id`, `agent_id`, `actor`, paging |

Existing endpoints for folder, code project, service and server selections and per-server SSH
settings stay and serve the Resources tab.

## UI

- **Agent page:** the Documents, Code projects, SSH, MCP and Services tabs become:
  - **Tools** — a checklist grouped Documents / Code / SSH / Inventory / MCP servers, each with
    its description and a usage-docs preview.
  - **Resources** — existing pickers for servers (per-server instructions, Configure SSH
    connection), services (showing the servers and code projects they bring in), document
    folders and code projects.
  Skills, Prompts, Versions and Test stay.
- **Tools page** (Administration → Tools): every tool, built-in and MCP, with its functions and
  editable usage docs. The MCP servers page keeps managing connections; their tool appears
  automatically.
- **Agents list:** shows the picked tools.
- **Agent Wizard:** proposes tools and resources instead of capability switches.
- **Reader assistant card:** violations from the tool rule.
- **Activity:** new **SSH actions** list with server/agent/user filters.

## Testing

Test-first; fake model; the admin-api SSH test server fixture.

- Migration: every conversion row; MCP selections → MCP tools; refusal when per-server
  overrides exist; column/table drops; up/down.
- `upgrade_snapshot` for pre-0061 snapshots, including grounded documents.
- Resource expansion: services → deployment servers and code projects; disabled or
  non-SSH servers excluded; archived projects excluded.
- `get_tools` per picked tool and empty resources; call-time refusals for an unpicked tool and
  for a server/path/project outside effective resources.
- SSH functions: exact commands; rejection of unsafe unit/container names, `since`, `lines`,
  URLs and paths (`..`, outside roots, symlink escape); only `run_command` uses sudo; an agent
  with only `ssh_logs` cannot call any other SSH function even with crafted calls.
- `ssh_actions` rows for succeeded, failed and refused calls with the real actor.
- Prompt order; unpicked tools add no text; usage edits reach new turns.
- Reader tool rule.
- UI: Tools tab, Resources tab with service expansion, Tools page, SSH actions list, conflict
  messages.
- Existing agent Test, board, documentation generation and Reader tests pass after migration.

## Out of scope

- Server-side lock-down (restricted key with forced command / `ka-diag`).
- Server profiles (next spec, 0062) — they attach to any agent with an SSH tool, for its
  effective servers.
- Reader Support tier and group gating.
- Runbooks; admin-defined command templates.
- Publishing built-in tools as an MCP server; a Grafana tool.
