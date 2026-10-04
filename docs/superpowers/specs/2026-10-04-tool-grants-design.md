# Per-agent tool grants and SSH policies

Status: design approved 2026-10-04; awaiting spec review.

## Goal

An agent is its instructions plus a set of **tool grants**. Each grant gives the agent one
toolset (Documents, Code, Shell, SSH, Inventory, or one MCP server) with a **scope** (which
folders, projects, servers or services), an **enforced policy** (which actions exist), and
optional **grant instructions** that are added to the toolset's default instructions. New
kinds of agents — a Docs agent, a Server agent, a Log agent safe for lower-privileged users —
are made by combining a prompt with grants, not by adding agent-level flags.

Success criteria:

- Today's agents behave the same after migration (except the documented Documents change).
- An agent with an SSH grant under the **logs** policy can only read logs on its granted
  servers; it has no way to run a free-form command, even if prompted to.
- The model only sees instructions and actions for toolsets it is granted.
- Every SSH call is recorded with the real user behind it.

## Context and roadmap

Spec 2 of 4 (order changed during design): **tool grants** (this spec) → server profiles
(`2026-10-04-server-profiles-design.md`, migration 0062) → Reader Support tier and group
gating → runbooks. It replaces the capability switches introduced over earlier migrations
and the `documents_grounded` flag from the Reader chat work.

## Agreed decisions

| Decision | Choice |
| --- | --- |
| Grant unit | One **toolset** per integration; its policy chooses actions inside it. |
| Policies | Built-in, named, defined in code per toolset; a grant may disable individual actions (narrow only). No admin-defined commands. |
| Skills | Remain instruction blocks attached to the agent (not a toolset). |
| Services | Become the **inventory** toolset (context + lookup). |
| Storage | `ka.agent_tool_grants` + seeded `ka.toolsets`; scopes stay in today's join tables. |
| Instructions | Toolset default instructions (Tools page) + per-grant instructions; only granted toolsets contribute text. |
| Enforcement | Actions outside the policy are never offered to the model and are re-checked when called. |

## Toolset catalog (defined in code)

| Toolset | Actions | Scope | Policies |
| --- | --- | --- | --- |
| `documents` | `search_documents`, `read_document`, `report_not_covered` | `agent_knowledge_folders`, `agents.all_folders`, `agents.include_subfolders` | `answer` = search, read · `answer_report` = search, read, report_not_covered |
| `code` | `list_files`, `read_file`, `search_files`, `code_search`, `ast_grep`, `code_graph` (only when graphs are configured) | `agent_repositories`, `agents.all_repositories` | `read` = all actions |
| `shell` | `shell_run` (sandbox: no network, granted code folders read-only, scratch writable) | the agent's code grant scope | `sandbox` |
| `ssh` | `run_command`, `service_status`, `service_logs`, `container_list`, `container_logs`, `tail_log`, `disk_usage`, `memory`, `listening_ports`, `http_check` | `operations_servers`; per-server `agent_configs.instructions`; `agent_server_connections` | `full` = all actions, sudo per server setting · `readonly` = all except `run_command`, never sudo · `logs` = `service_logs`, `container_logs`, `tail_log`, never sudo |
| `inventory` | `lookup_service`; granted services' descriptions attached as context | `agent_services` | `read` |
| `mcp` (one grant per MCP server) | the server's discovered tools | the grant's `mcp_server_id` | `all` |

Default instructions shipped with the seeds:

- `documents`: how to search and read; for `answer_report` grants the citation rules
  (formerly `grounding.POLICY`) are appended automatically: answer only from returned
  excerpts, cite `[n]`, never cite unreturned numbers, call `report_not_covered` when the
  excerpts do not answer.
- `code`, `shell`: the guidance currently hard-coded in `get_prompt`.
- `ssh`: use the configured server IDs, follow per-server instructions, prefer structured
  actions, never request root unless allowed.
- `inventory`, `mcp`: short usage notes.

### Structured SSH actions

The model passes typed arguments; admin-api builds the command; arguments are validated
before any connection:

| Action | Arguments | Command |
| --- | --- | --- |
| `service_status` | `unit` | `systemctl status <unit> --no-pager` |
| `service_logs` | `unit`, `since` (e.g. `30m`, `2h`, `1d`, max `7d`), `lines` 1–2000 | `journalctl -u <unit> --since -<since> -n <lines> --no-pager` |
| `container_list` | — | `docker ps --format …` |
| `container_logs` | `container`, `since`, `lines` | `docker logs --since <since> --tail <lines> <container>` |
| `tail_log` | `path`, `lines` | `tail -n <lines> <path>` |
| `disk_usage` | — | `df -hT -x tmpfs -x devtmpfs` |
| `memory` | — | `free -m` |
| `listening_ports` | — | `ss -tlnp` |
| `http_check` | `url` (http/https to the server's own hostname or 127.0.0.1/localhost) | `curl -sS -o /dev/null -w '%{http_code} %{time_total}' --max-time 10 <url>` |
| `run_command` | `command`, `as_root` | today's behaviour |

- `unit` and `container` match `^[A-Za-z0-9@._:-]{1,200}$`; every argument is shell-quoted.
- `tail_log` paths must be absolute, contain no `..`, and lie under `/var/log` or a log
  location recorded for a deployment on that server; the remote command resolves the path
  with `readlink -f` and refuses if the resolved path leaves those roots.
- Execution reuses the existing SSH runner (timeout, `setpriv --no-new-privs`, 32 KiB
  output cap). Sudo is used only by `run_command` under `full` when the server allows it;
  `service_logs` and `container_logs` may use `sudo -n` only under `full`.

## Data model — migration `0061_tool_grants`

```sql
CREATE TABLE ka.toolsets (
    id           text PRIMARY KEY CHECK (id IN ('documents','code','shell','ssh','inventory','mcp')),
    instructions text NOT NULL CHECK (length(instructions) <= 20000),
    revision     integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    updated_by   uuid REFERENCES ka.principals(id),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE ka.agent_tool_grants (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id         text NOT NULL REFERENCES ka.agents(id) ON DELETE CASCADE,
    toolset          text NOT NULL REFERENCES ka.toolsets(id),
    mcp_server_id    uuid REFERENCES ka.mcp_servers(id) ON DELETE CASCADE,
    enabled          boolean NOT NULL DEFAULT true,
    policy           text NOT NULL CHECK (policy ~ '^[a-z_]{1,40}$'),
    disabled_actions text[] NOT NULL DEFAULT '{}',
    instructions     text NOT NULL DEFAULT '' CHECK (length(instructions) <= 20000),
    revision         integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    updated_by       uuid REFERENCES ka.principals(id),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    CHECK ((toolset = 'mcp') = (mcp_server_id IS NOT NULL))
);
CREATE UNIQUE INDEX agent_tool_grants_toolset ON ka.agent_tool_grants(agent_id, toolset) WHERE toolset <> 'mcp';
CREATE UNIQUE INDEX agent_tool_grants_mcp ON ka.agent_tool_grants(agent_id, mcp_server_id) WHERE toolset = 'mcp';

CREATE TABLE ka.ssh_actions (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_id    uuid NOT NULL REFERENCES ka.projects(id),
    run_id        uuid,
    session_id    uuid,
    agent_id      text NOT NULL,
    server_id     uuid,
    actor_id      uuid REFERENCES ka.principals(id),
    action        text NOT NULL,
    arguments     jsonb NOT NULL,
    policy        text NOT NULL,
    outcome       text NOT NULL CHECK (outcome IN ('succeeded','failed','refused')),
    exit_code     integer,
    output_bytes  integer,
    duration_ms   integer,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ssh_actions_recent ON ka.ssh_actions(project_id, created_at DESC);
```

`ssh_actions` rows are only inserted; `server_id`/`agent_id` are kept as plain values so the
audit survives deletions.

### Conversion of existing agents

| Today | Grant |
| --- | --- |
| `documents_enabled` | `documents`, policy `answer_report` if `documents_grounded` else `answer` |
| `repositories_enabled` | `code` (`read`) and `shell` (`sandbox`) |
| `ssh_enabled` | `ssh` (`full`) |
| `mcp_enabled` + agent-level MCP selections (`agent_config_mcp_servers` of the agent's `server_id IS NULL` config) | one `mcp` grant (`all`) per selected server |
| `services_enabled` | `inventory` (`read`) |

- A capability that is off but has saved selections becomes a grant with `enabled = false`.
- The coordinator gets no grants.
- The migration raises an error naming the agents if any per-server skill or MCP override
  exists (`agent_configs.server_id IS NOT NULL AND (NOT inherit_skills OR NOT inherit_mcp_servers)`).
- Then it drops `agents.documents_enabled`, `documents_grounded`, `repositories_enabled`,
  `ssh_enabled`, `mcp_enabled`, `services_enabled`, the columns
  `agent_configs.inherit_skills` and `inherit_mcp_servers`, and per-server rows of
  `agent_config_skills` and `agent_config_mcp_servers`.
- `skills_enabled` and skill selections stay.
- The down migration recreates the dropped columns from grants (`answer_report` →
  `documents_grounded = true`); policies other than the defaults and disabled actions are lost.

## Configuration and versions

- `get_agent_config` returns `config['tools']`, a list of
  `{toolset, mcp_server_id, enabled, policy, disabled_actions, instructions,
  default_instructions, scope}` where `scope` holds the resolved folders, projects, servers
  (with per-server instructions and connection data) or services, in the shapes the current
  `documents`/`code`/`ssh`/`services`/`mcp` blocks use. The old capability blocks are no
  longer produced.
- Agent version snapshots store this list, including `default_instructions`, so a version
  reproduces exactly what the model saw.
- `upgrade_snapshot(config)` converts snapshots saved before 0061 using the conversion table
  above; it runs wherever snapshots are loaded (sessions, versions, board).
- Grants and toolset defaults are revision-checked (409 "…changed. Reload before saving.")
  and audited in `ka.audit_events`.

## Runtime

- `get_tools(config)` returns functions only for enabled grants, from the grant's policy minus
  `disabled_actions`, and only when the scope has at least one usable resource.
- Each action function re-checks the grant from the run context (enabled, policy, disabled
  actions, scope membership) and refuses with a message to the model otherwise; refusals of
  SSH actions are written to `ssh_actions` with `outcome = 'refused'`.
- `get_prompt(config)` order: system prompt → additional instructions → skills → for each
  enabled grant, its toolset's `default_instructions` then the grant's `instructions` →
  resource context (folder descriptions, code projects, SSH servers with per-server
  instructions, inventory services) → the existing secrets/resources rules. Toolsets without
  an enabled grant contribute nothing.
- Every SSH action writes one `ssh_actions` row with the run, session, agent, server, the
  run's actor (the real user), arguments (redacted), policy, outcome, exit code, output size
  and duration.
- `agents.agent()` and `create_agent()` signatures are unchanged.

### Reader Knowledge tier

`knowledge_violations(config)` becomes grant-based: allowed enabled grants are `documents`
with policy `answer_report` (required) and `inventory`; any enabled `code`, `shell`, `ssh` or
`mcp` grant is a violation ("uses SSH", "uses code projects", "uses the shell", "uses MCP
tools"); a missing or `answer`-policy documents grant gives "document search with gap
reporting is off". Disabled-agent and no-model reasons stay.

## API

| Method and path | Purpose |
| --- | --- |
| `GET /api/toolsets` | Catalog: toolsets, actions, policies, default instructions, revisions |
| `PUT /api/toolsets/{id}` | Edit default instructions `{instructions, revision}` |
| `GET /api/agents/{id}/tools` | The agent's grants with scopes |
| `POST /api/agents/{id}/tools` | Add a grant `{toolset, mcp_server_id?, policy, …}` |
| `PUT /api/agents/{id}/tools/{grant_id}` | Update policy, disabled actions, instructions, enabled, scope `{…, revision}` |
| `DELETE /api/agents/{id}/tools/{grant_id}` | Remove a grant `{revision}` |
| `GET /api/ssh-actions` | Audit list with filters `server_id`, `agent_id`, `actor`, paging |

The existing per-server SSH endpoints (`/api/agents/{id}/servers/{sid}/ssh`,
`/api/operations/configs/{sid}`) stay and serve the SSH grant's scope editor. The capability
fields are removed from `PUT /api/agents/{id}`.

## UI

- **Agent page:** the Documents, Code projects, SSH, MCP and Services tabs are replaced by a
  **Tools** tab (Skills, Prompts, Versions, Test stay). It lists grant cards (toolset, enabled
  switch, policy, scope summary, Edit) and **Add tool** (toolsets the agent lacks, and each
  MCP server).
- **Grant editor** (side sheet): Policy select; Actions checkboxes (the policy's actions,
  narrow only); Scope (existing folder/project/server/service pickers; SSH keeps per-server
  instructions and Configure SSH connection); read-only default instructions with a link to
  the Tools page; Grant instructions; Save; Remove tool.
- **Tools page** (Administration → Tools): each toolset's actions, policies and editable
  default instructions.
- **Agents list:** the Capabilities column shows grants with policies, e.g.
  "Docs (report gaps) · SSH (logs)".
- **Agent Wizard:** proposes grants instead of capability switches.
- **Reader assistant card:** violations come from grants.
- **Activity:** new **SSH actions** list with server/agent/user filters.

## Testing

Test-first; fake model; the admin-api SSH test server fixture.

- Migration: every conversion row; disabled capabilities → disabled grants; MCP selections →
  grants; refusal when per-server overrides exist; column drops; up/down.
- `upgrade_snapshot` for pre-0061 snapshots, including grounded documents.
- `get_tools` per policy, disabled actions, empty scope, disabled grant; the call-time
  re-check refusing a removed action and a server outside scope.
- Structured SSH actions: exact commands from arguments; rejection of unsafe unit/container
  names, `since`, `lines`, URLs and paths (`..`, outside roots, symlink escape); no sudo
  outside `full`; `logs` policy cannot reach `run_command` even with crafted calls.
- `ssh_actions` rows for succeeded, failed and refused calls with the real actor.
- Prompt assembly order; ungranted toolsets add no text; `answer_report` appends the
  citation rules.
- Reader tier on grants.
- UI: Tools tab, grant editor, add/remove, Tools page, SSH actions list, conflict messages.
- Existing agent Test, board, documentation generation and Reader tests pass after migration.

## Out of scope

- Server-side lock-down (restricted key with forced command / `ka-diag`).
- Server profiles (next spec, 0062).
- Reader Support tier and group gating.
- Runbooks; admin-defined command templates.
- A Grafana toolset.
