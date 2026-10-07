# Knowledge agents: one agent model, two runtimes — design

Date: 2026-10-07. Status: draft for review. Builds on
`2026-10-07-claude-code-agents-design.md` and replaces its "registration only" rule.

## Goal

Agents are configured the same way regardless of how they run.

- Every agent has an **Agent type**:
  - **Native** runs on the OpenAI Agents SDK, the existing runtime.
  - **Claude Code** runs on the Claude Agent SDK.
- Everything else is shared: model, instructions and prompt versions, skills, documents
  and RAG collections, code projects, SSH servers, MCP servers and services.
- The agent screens are identical for both types. Switching the type changes only the
  runtime, so the same agent can be compared on both.
- Both runtimes use one tool catalog and one session API.
- Documentation generation becomes a client: it picks an agent and a prompt, then
  starts a session.

## Code layout

A new package `services/admin-api/src/knowledge_admin/knowledge_agents/` owns agents.

| Module | Responsibility |
| --- | --- |
| `config.py` | The agent config model (one shape for both types) and loading the resolved snapshot (existing `get_agent_config`, moved) |
| `catalog.py` | The tool catalog: each tool's name, description, argument schema, which config enables it, and one implementation function |
| `native.py` | Native runtime: builds the OpenAI Agents SDK agent from the config and catalog (existing behaviour, moved) |
| `claude_code.py` | Claude Code runtime: builds `ClaudeAgentOptions` from the config, exposes catalog tools as an in-process SDK MCP server (existing worker pieces, moved) |
| `sessions.py` | The session API for both types: create, list, detail, send, stop, close, events (SSE), save draft as document (existing `claude_code_api.py`, generalised) |
| `gateway.py` | The tool gateway service used by Claude Code sessions |

The existing modules (`agents.py`, `claude_code_*.py`, `tools/*`, `agent_sessions.py`)
move into or behind this package step by step. Imports keep working until each step
removes them.

## Agent config

- **`ka.agents.runtime`** (`openai` | `claude_code`) is shown as **Agent type**
  (Native / Claude Code). The 0070 constraint that kept Claude Code agents
  registration-only is removed (new migration). Claude Code agents get the same
  columns, versions and prompt as native agents.
- **Model:**
  - **Native:** any chat model compatible with the native runtime (as today).
  - **Claude Code:** Claude connections only (Bedrock inference profiles or
    Anthropic). Switching the type to Claude Code with a non-Claude model asks the
    user to pick a Claude model first.
- **Claude Code tools** (Claude Code agents only):
  - switches for **Read**, **Grep**, **Glob** and **Bash**, all on by default, stored
    as `claude_code_tools text[]`;
  - with all four off, the agent only has catalog tools.
- **Everything else is identical:** name, description, when to use, enabled,
  instructions and prompt versions, skills, documents (collections, all collections),
  code projects (all or selected), SSH servers with per-server instructions and
  connection overrides, MCP servers, services, and the coordinator rules.

## Agent screens

- **All agents:** one list with an **Agent type** column.
  - **New agent** keeps one panel, but the **Generic agent** wizard gets an **Agent
    type** choice.
  - The separate Claude Code tab goes away, because both types are created the same
    way.
- **Agent page:** the same tabs for both types: General, Skills, Documents, Code
  projects, SSH, MCP, Services, Prompts, Versions and Test.
  - **General** shows **Agent type** and the model, filtered to models that work
    with that type.
  - Claude Code agents also see a **Claude Code tools** card on General with the four
    switches.
- **Test:** opens an interactive session (stream, send, stop) with the agent's saved
  config, for both types.

## Tool catalog

One definition per tool, used by both runtimes:

| Tool | Enabled by | Notes |
| --- | --- | --- |
| `search_documents`, `read_document`, `report_not_covered` | Documents | RAG collections of the agent |
| `code_graph`, `code_search`, `ast_grep` | Code projects | The agent's projects and their graphs |
| `list_files`, `read_file`, `search_files`, `shell_run` | Code projects or documents | Native only; Claude Code uses its own Read/Grep/Glob/Bash over the same folders |
| `ssh_run` | SSH | The agent's servers, their rules (sudo, timeout, host keys) |
| Service lookup | Services | Services with their deployments, servers and code projects |
| MCP server tools | MCP | The agent's MCP servers |

**Skills** are instructions. Both runtimes append the agent's enabled skills to the
system prompt.

- **Native** registers catalog tools as function tools, as today.
- **Claude Code** registers them in an in-process SDK MCP server named `knowledge`.
  Claude sees them as `mcp__knowledge__<tool>`. Each call is forwarded to the tool
  gateway.
- **Every tool enforces its own access at call time:** only the agent's own
  collections, projects, servers and MCP servers, and the rules configured for them.
  Instructions alone are never trusted for access.

## Tool gateway (Claude Code only)

Claude Code's container must stay isolated, because Bash can reach anything on its
networks. Catalog tools therefore run in a separate service.

- **New service `agent-tool-gateway`:**
  - It shares the internal network `agent-tools` with `claude-code-worker` and nothing
    else.
  - It is also on `default` (RAG, PostgreSQL, MCP servers) and `agent-shell` (SSH,
    shell-worker).
  - It does **not** trust forwarded identity headers. Its only route is
    `POST /tools/<name>`, authenticated by a **session tool token**.
- **Session tool token:**
  - a random token per session, created by the worker when the session starts and
    stored hashed in PostgreSQL with the session id and expiry;
  - the worker passes it only to its in-process tool functions;
  - it never reaches the Claude Code process environment, files or events, so Bash
    cannot read it (the CLI runs as an unprivileged slot user).
- **On each call, the gateway:**
  1. looks up the session and its agent config snapshot;
  2. checks the tool is enabled for that agent;
  3. runs the catalog implementation with the same access checks as native;
  4. returns the result (redacted with the session's secrets) to the worker, which
     gives it to Claude.
- **SSH:**
  - SSH keys stay in the gateway and are decrypted per call, as the native runtime
    does.
  - The Claude Code container never holds SSH keys.
  - There is no direct SSH from Bash: the `claude-code` network has no route to
    servers beyond the internet, and no keys.
- **Network test:** `claude-code-worker` reaches only PostgreSQL (`claude-code`) and
  the gateway (`agent-tools`), and the gateway port accepts only token-authenticated
  tool calls.

## Session API for both types

- **Generalised endpoints:** `claude_code_api.py` becomes `knowledge_agents/sessions.py`
  with the same calls: create, list, detail, send, stop, close, events (SSE), save
  document. The `/api/claude-code/sessions*` routes move to `/api/agent-sessions*`.
- **Workers:**
  - **Native** sessions run in a new `agent-session-worker` on the default network.
    It uses the same claim, lease, queue and event code, and runs turns with the
    native runtime and the catalog tools directly.
  - **Claude Code** sessions keep running in `claude-code-worker`.
- **Events:** both workers emit the same event kinds (input, message, reasoning,
  tool_call, tool_result, usage, error, end) under one run key format `session:<id>:<turn>`.
- **Test tabs:** the agent Test tab and the existing test chats move onto sessions in
  step 3. The multi-agent board keeps its own runner until a later step.

## Documentation generation

**Docs → Generate documents → New** has one way to generate: with an agent. The
**Service**, **Whole environment** and **Code project** modes and their scope
selector are removed.

The form, top to bottom:

1. **Agent** (Claude Code agents until step 3, then either type).
2. **What to document:** one or more of **Code**, **Environment**, **Services** and
   **Servers** (checkboxes). Each checked choice shows its picker:
   - **Code:** one or more code projects;
   - **Environment:** one or more environments;
   - **Services:** one or more services;
   - **Servers:** one or more servers.
   At least one item must be picked.
3. **Instructions** (optional): extra instructions for this run.
4. **Document name** and **folder**, as today.

- **There is no model or prompt field.** The agent runs with its own model, prompt,
  instructions, skills and tools; the form never overrides them. A documentation agent
  therefore carries its documentation prompt itself.
- **What the agent receives:** its system prompt, then one message listing what to
  document and the instructions.
  - Code projects become working folders (Claude Code) or code roots (native).
  - Environments are described as text: their servers, services, deployments and
    linked code projects. Linked code projects' folders are also working folders.
  - Services are described as text: their deployments, servers and linked code
    projects. Linked code projects' folders are also working folders.
  - Servers are described as text: name, address and description. Server access goes
    through the agent's own SSH configuration (step 2), never through this form.
- **The form's only product is that message.** It is a normal first message to the
  agent, so it is exactly what a user could have typed. Example:

  ```
  Document the following.

  ## Code
  - Payments API — /work/data/code/payments-api

  ## Services
  - Payments — deployed on prod-1 (production); code: Payments API

  ## Servers
  - prod-1 (10.0.0.5): Payments host

  ## Instructions
  Focus on the public API.
  ```

  The only thing it adds besides the message is read access to the listed code folders
  (Claude Code working folders, native code roots), because an agent cannot read a
  folder it has not been given.
- **The free folders and notes fields go away.**
- **The draft is saved as a document,** as today.
- **Sessions become Generate documents.** **Docs → Generate documents** lists the
  sessions (status, agent, tokens, saved document); **New** opens the form above and
  submitting creates a session and opens it. The separate **Sessions** menu item goes
  away, and session URLs move under `/documentation/generations`.
- **The session page fits the screen:**
  - Activity and Draft are fixed-height panels side by side, each with its own scroll;
    the page does not grow and the message box stays visible.
  - Activity follows new events while you are at the bottom; scrolling up stops
    following and shows **Jump to latest**.
  - Opening a session loads only the latest 100 events and the live stream continues
    after the newest. Scrolling near the top loads the previous 100, keeping your
    position, until **Beginning of the session**. Older pages come from
    `GET …/sessions/{id}/events/history?before=<event id>&limit=100`, redacted like the
    stream.
- **The old one-shot generation is removed completely:** its page, API routes, worker
  phases, tables (dropped in a migration, with their history) and any files it wrote
  for runs. Saved documents stay in **Docs → Documents**.

## Delivery steps (each ships alone)

1. **Unified config and screens.**
   - The migration lifts the registration-only rule and adds `claude_code_tools`.
   - Agent type appears in the wizard and on General.
   - Claude Code agents get all tabs.
   - Claude Code sessions use the agent's instructions, prompt and skills, plus its
     code projects and folders as working folders.
   - Catalog tools are not given to Claude Code yet; the UI says so on each tool tab
     for Claude Code agents.
2. **Tool catalog and gateway:** the `knowledge_agents` catalog, the
   `agent-tool-gateway` service, session tool tokens and the in-process MCP server.
   Claude Code agents can then use documents, code graph, SSH, MCP and services.
3. **Native agents through the session API:** `agent-session-worker`, the
   `/api/agent-sessions` routes, and the Test tab on sessions for both types.
4. **Documentation is generated only with an agent:** one form (agent, what to
   document: code / environment / services / servers, instructions, name, folder);
   sessions are renamed Generate documents; the old one-shot generation, its tables,
   history and files are removed. Planned right after step 1.

## Errors

| Problem | Behaviour |
| --- | --- |
| Claude Code type with a non-Claude model | 400 "Choose a Claude model for Claude Code agents" |
| Tool not enabled for the agent | The gateway refuses with a tool error the model sees; nothing runs |
| Expired or unknown session tool token | 401 from the gateway; the turn records a tool error |
| Gateway unavailable | The tool call fails with "Agent tools are unavailable"; the session continues |

## Testing

- **Config:** the migration (constraint lifted, `claude_code_tools`), type switching
  rules, identical config round-trip for both types.
- **Catalog:** each tool's enable rule and access checks, shared by both runtimes.
- **Gateway:**
  - a token is required and scoped to its session;
  - disabled tools are refused;
  - results are redacted;
  - the compose network test: the worker reaches only PostgreSQL and the gateway, and
    Bash has no token.
- **Claude Code runtime:**
  - the in-process MCP server lists exactly the enabled tools;
  - the Claude Code tools switches map to `tools`/`allowed_tools`;
  - skills are appended.
- **Sessions:** both workers emit the same events, and the Test tab works for both
  types.
- **UI:**
  - identical tabs for both types;
  - Agent type and model filtering;
  - the Claude Code tools card;
  - the documentation form (agent, what to document, instructions, name, folder).
- **Real run:** one Claude Code session that calls `search_documents`, `code_graph` and
  `ssh_run` through the gateway.

## Out of scope

- The multi-agent board on sessions.
- Write tools (Edit or Write) for Claude Code.
- Per-tool usage limits or cost limits.
