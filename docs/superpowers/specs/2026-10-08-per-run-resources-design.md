# Per-run resources (role agents) — design

Date: 2026-10-08. Status: draft for review. Builds on
`2026-10-07-knowledge-agents-design.md`.

## Goal

An agent either works with **fixed** resources, as today, or is a **role agent** that receives
its resources **per run**. A role agent defines what it does: prompt, instructions, skills and
tools. Each run tells it what to work on: code projects, document collections, servers or
services.

Examples:
- a **Code analyst** asked about `payments-api` in one run and `helpers` in the next;
- an **SSH agent** given the staging servers of the environment being documented.

A documentation builder can later call several role agents and pass each one the run's sources.

## Agent setting: Resources

- **New column** `ka.agents.resources text NOT NULL DEFAULT 'fixed' CHECK (resources IN ('fixed','per_run'))`.
  - Existing agents stay **fixed**.
  - The coordinator must stay fixed (database check, like
    `claude_code_agent_is_not_the_coordinator`).
- **Versioned:** the value is part of the configuration snapshot, like `claude_code_tools`.
  Activating a version applies its mode. Versions saved before this change count as `fixed`.
- **Meaning of the existing selections:**

  | Selection | Fixed | Per run |
  | --- | --- | --- |
  | Code projects: all / selected | The projects the agent always uses | The projects a caller **may** pass (all = any) |
  | Documents: all collections / selected | The collections the agent always uses | The collections a caller may pass |
  | SSH servers (with per-server instructions and connection overrides) | The servers the agent always uses | The servers a caller may pass; their instructions and overrides still apply |
  | Services | The services the agent always uses | The services a caller may pass |
  | Capability switched off | Not used | Not used: callers cannot pass that kind |

  No new tables. Skills and MCP servers stay fixed in both modes; they describe *how* the agent
  works, not *what* it works on.

## Run resources

- **What a run carries:** every run of a per-run agent carries **run resources**, for example
  `{"code_project_ids": [...], "collection_ids": [...], "server_ids": [...], "service_ids": [...]}`.
- **Validation when the run starts:**
  - each id must exist in the project, be enabled and not archived;
  - its kind must be switched on for the agent;
  - it must be inside the agent's allowed selection;
  - a service brings its deployments' servers and code projects, which must also be allowed.

  Otherwise the run is refused with a message naming the resource.
- **Stored with the run:** new nullable column `run_resources jsonb` on `ka.agent_sessions_cc`
  (documentation sessions), `ka.agent_sessions` (Test chats) and `ka.operations_runs`. Every
  record shows what the run was given.
- **Enforcement without changing tools:**
  - **Native agents:** the runner builds the run's effective configuration by narrowing each
    resource list in the snapshot (`code.projects`, `documents.collections`, `ssh.servers`,
    `services.items`) to the run resources. The existing tools already allow only what the
    configuration lists, so a tool call can never reach a resource the run was not given.
  - **Nothing passed for an enabled kind:** that list becomes empty and its tools answer "No …
    was given for this run". A per-run agent never falls back to "all".
  - **Claude Code agents:** run resources become their working folders (code projects). Other
    kinds reach them with the tool gateway (knowledge agents step 2).
- **Fixed agents ignore run resources.** A caller passing resources to a fixed agent is refused
  ("This agent uses fixed resources").
- **Agents calling agents** (future builder): a caller can only pass resources it has itself
  for its current run.

## Screens

- **General tab:**
  - **Resources: Fixed / Per run**, next to Agent type, with one line of help each;
  - the coordinator shows Fixed with no choice;
  - the New agent wizard asks the same.
- **Capability tabs** (Code projects, Documents, SSH, Services):
  - **Per-run agents:** a note at the top: "Per run: callers may pass any of the projects
    selected here. All projects = any project." The same wording adapts per kind.
  - **Fixed agents:** unchanged.
- **All agents list:** a **Resources** column.
  - **Fixed** shows "Fixed".
  - **Per run** shows the kinds it accepts and their limit, for example "Per run · code (any)"
    or "Per run · servers (2)".
- **Test tab (native):** for a per-run agent, a **Resources for this run** picker above the
  message box. It has one multi-select per enabled kind, offering only allowed resources. The
  choice is fixed for the conversation, like the configuration it starts with, and is shown in
  **Show config**.
- **Generate documents:**
  - **Per-run agents** receive the form's sources as run resources (code projects, services,
    servers).
  - **Fixed agents:** the sources stay in the first message, as today.

## Errors

| Problem | Behaviour |
| --- | --- |
| Resource not allowed for the agent | 400 "<kind> <name> is not allowed for this agent" |
| Resource missing, disabled or archived | 400 naming it |
| Resources passed to a fixed agent | 400 "This agent uses fixed resources" |
| Coordinator set to per run | 400; database check as backstop |
| Tool used for a kind with nothing passed | Tool error the model sees: "No <kind> was given for this run" |

## Testing

- **Migration:** default `fixed`, values check, coordinator check, up/down.
- **Config:** snapshot carries `resources`; version round-trip; old versions read as `fixed`.
- **Run start:** validation (allowed, existing, enabled, kind switched on), services expand to
  servers and projects, refusal for fixed agents.
- **Narrowing:**
  - a per-run native run's effective configuration contains exactly the run resources;
  - file, code graph, document and SSH tools refuse others;
  - nothing passed leads to tool refusals, never "all".
- **Records:** `run_resources` saved on sessions, Test chats and runs.
- **UI:**
  - the General setting;
  - the list column;
  - the capability tab notes;
  - the Test tab picker, sending its choice and showing it in the configuration view.

## Out of scope (next specs)

- Built-in role agents installed at start-up (Code analyst, SSH agent, Docs agent), with the
  update notice.
- Optional per-run model: an allowed-models list on the agent, chosen per run and recorded.
- Code analyst question sets (`helpers`, responsible gaming) and their evaluation runs.
- The documentation object and the builder agent.
