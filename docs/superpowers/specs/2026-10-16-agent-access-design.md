# Agent access: one access object, tools always on

Date: 2026-10-16. Status: agreed in conversation (2026-10-16), for review.

## Why

What an agent may use is spread over about ten tables and several mechanisms:

- a saved agent's configuration: `ka.agents` (with on/off switches per kind and "all collections / all projects"),
  `ka.agent_tools` (a mode per tool), `ka.agent_repositories`, `ka.agent_collections`, `ka.agent_services`,
  `ka.operations_servers`, per-server overrides in `ka.agent_configs` / `ka.agent_config_skills` /
  `ka.agent_config_mcp_servers` / `ka.agent_server_connections`, and `ka.skills_agents`;
- at run start `get_agent_config` reads all of it into one JSON, `run_resources.resolve` narrows it and adds
  `run_resources` and `chosen_resources`; ad-hoc runs build the same JSON in code (`ephemeral.py`);
- a tool is offered only when its mode is on and its "need" is met;
- every enabled skill's full text is pasted into the prompt;
- the model learns its resources from a JSON block appended to the prompt, a `Resource:` line and `get_resource`;
  each tool checks a different allowlist section of the pinned JSON (`capabilities/context.py` `ACCESS`).

## Principles (binding)

- **Simple code, no unnecessary abstractions.** One obvious function over layers, registries or strategy classes.
- **Testable in isolation.** The access rules are pure functions over the access object — no database, no framework,
  no agent runtime. Every tool's access behaviour is tested against them in one table-driven test.
- **Tools always on; the access object decides.** A tool that is not allowed to act refuses with a clear message.
- **Narrow, never widen.** A run can use the agent's access or less, never more.

## The access object

One JSON object on the saved agent, copied (or built) into the run:

```json
{
  "resources": [
    {"type": "server", "id": "…"},
    {"type": "document", "id": "…", "editable": true},
    {"type": "skill", "id": "…"}
  ],
  "roles": {"content_editor": false}
}
```

- **Resource types:** `server`, `service`, `environment`, `code_project`, `collection`, `document`, `skill`,
  `mcp_server`. Entries are references (type + id); a `document` entry may carry `editable` (default false).
- **Roles:** `content_editor` (default false). Off: the agent cannot change content — `propose_service`,
  `propose_document`, `save_document`, `edit_document` refuse with `This agent may not change content (content editor is
  off).` On: they work, still only on resources in the list (a proposal needs its resource in the list; editing needs
  the document in the list and `editable`).
- **Stored:** `ka.agents.access jsonb` (saved agents) and `ka.agent_sessions.access jsonb` (runs).

### Building a run's access (at run start)

- **Saved agent:** the agent's access, or the subset the caller picked (Test tab, event handler, chat settings): only
  resources already in the agent's list, and roles only switched off, never on.
- **Ad-hoc run (no saved agent):** what was picked — a resource page's resource; the settings sheet's choices; a
  document run's document. Runs where a person explicitly asked for writing — Documents → New, Ask agent on a document,
  the Propose wizard — get `content_editor: true`; a plain resource chat gets `false`.
- **Children expanded:** direct children of each given resource are added, marked: environment → its services and
  servers; service → its servers and code projects; server → the services deployed on it. Each run entry is stored as
  `{type, id, name, description_short, source}` with `source` `given` or `via <type> <id>`, so the run's list is
  explicit and visible in Debug.
- **Pinned:** the run's access never changes after start (later edits to the agent apply to new runs).

## The access rules (pure module)

`knowledge_admin/access.py` — plain functions over the access object (dicts), no I/O:

- `build_run_access(agent_access, picked, children) -> dict` — narrow + expand (children supplied by the caller, already
  loaded); raises on widening.
- `can_use(access, type, id) -> bool`
- `can_write_document(access, document_id) -> bool` — `content_editor` and the document is in the list and `editable`.
- `can_propose(access, type, id) -> bool` — `content_editor` and the resource is in the list.
- `can_create_document(access, built_for: list[(type, id)]) -> bool` — `content_editor` and every built-for resource is in
  the list.
- `refusal(kind, type, id) -> str` — the exact messages: `This <type> is not available in this run.` and the
  content-editor message above.

Every tool calls these; nothing else decides access.

## Tools

- **All tools always on.** Every run gets every catalog tool and the tools of the MCP servers in its list. Gone: the
  per-agent Tools tab, `ka.agent_tools` modes and usage notes, the "needs" system (`needs_met`, `RUN_ONLY_NEEDS`),
  the run-request tool switches and `proposal_tools`.
- **Each tool checks access first** with the functions above and returns the refusal text as its result when not
  allowed; a tool with nothing of its kind in the list says so (`This run has no <type>s.`).
- **Tools rewritten for testability (own task):** each tool is split into a pure access check (arguments + access →
  allowed or refusal) and the work (database/SSH/file I/O). The table-driven test runs every tool's check against a set
  of access objects (empty; one server; a server with children; a read-only document; an editable document with and
  without `content_editor`; a resource not in the list) and asserts allowed/refused with the exact text — without a
  database or an agent run.
- **`get_available_resources(type?)`** (new): returns the run's list — `{type, id, name, description_short, source}` —
  optionally filtered by type. `get_resource` stays for one resource's details.
- **The prompt** no longer carries the resource JSON block; a resource run keeps its one `Resource:` line.

## Skills

- Following the Agents SDK convention (its shell/sandbox skills: the list at creation, full text on use): the run's
  skills (name + description) go into the agent's instructions at creation, using the SDK's skills section wording;
  **`load_skill(name)`** returns the skill's full instructions from the database (refused when the skill is not in the
  run's list). Skill text is no longer pasted into the prompt.

## Kept

Model, system prompt, the agent's instructions (moved from `ka.agent_configs` to `ka.agents.instructions`), experts,
subagents (they narrow the access object with `build_run_access`), "answer only from documents", the Debug tab (shows
the access object: resources with source, roles).

## Delivery (three phases, each mergeable)

1. **Run side.** `ka.agent_sessions.access`; `access.py`; run start builds the access object (from today's agent
   tables for saved agents, from the pick for ad-hoc runs); `get_available_resources`; every tool checks access through
   `access.py` (including the content-editor role); the resource JSON block leaves the prompt; Debug shows the access
   object. **Tool rewrite task:** split each tool into check + work and add the table-driven access test for all tools.
2. **Agent side.** `ka.agents.access` replaces the resource tables, per-server overrides, on/off switches, "all"
   switches and fixed/per-run modes (test data: converted where simple, otherwise dropped); the agent editor gets a
   Resources picker writing the list and a **Content editor** switch; event handlers and the Test tab narrow it.
3. **Tools and skills.** All tools always on; the Tools tab, `ka.agent_tools`, needs and run tool switches removed;
   skills listed at creation + `load_skill`.

## Out of scope

- An `operator` role for commands that change servers (possible later; `ssh_run` keeps the server's own SSH settings
  and `allow_sudo`).
- Loading tool schemas on demand (all tools are sent; there are about 22).

## Testing

- `access.py` unit tests: narrowing (subset ok, widening refused, role only switched off), child expansion and sources,
  every `can_*` rule, refusal texts.
- Table-driven test: every tool × the access-object set → allowed or the exact refusal, without a database.
- Integration: a saved-agent run and an ad-hoc resource run store the expected access object; `get_available_resources`
  returns it; Debug shows it; a plain resource chat cannot propose or edit; a Documents → New run can create its
  document; a Propose wizard run can propose; load_skill returns a listed skill and refuses another.
