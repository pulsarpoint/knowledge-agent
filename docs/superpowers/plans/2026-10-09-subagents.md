# Subagents and Tool Defaults Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A native agent can start **subagents** with `run_subagent` — helpers with a task, a subset of its tools and
resources and no view of the conversation — that run in parallel inside a session turn, are recorded in the same turn
record and appear in the run workspace; in the same step, switching a capability on (fixed agents) or making an agent
per run switches all tools of its capabilities on.

**Architecture:**
- **Tool defaults (Task 1):** `tools/catalog.capability_tools(capability, config)` (every tool whose need belongs to the
  capability; `report_not_covered` only while grounded) and `enabled_capability_tools(config)`. `tools_api.
  switch_on_defaults` upserts them: a capability switched on → its tools; a per-run agent that becomes per run or gets
  a capability switched on → every enabled capability's tools. The wizard uses `enabled_capability_tools` when it is
  not sent a tool list. Saves that switch nothing on change no row.
- **The switch (Task 2):** the `run_subagent` row in `ka.agent_tools` (`mode='on'`, `subagent_allowed=false`) *is*
  **May start subagents**. `GET/PUT /api/agents/{id}` carry `subagents`; `PUT /agents/{id}/tools` keeps the row on,
  refuses it while off and now stores `subagent_allowed`; the configuration's tool items carry `subagent_allowed`.
  Catalog entry `run_subagent` (group `agents`, needs none, never on by default) whose run is
  `knowledge_agents/subagents.run_subagent`.
- **Checks and sub-config (Task 3, pure):** `subagents.check_task`, `subagent_config(config, tools, resources, *,
  offered, mcp_tools)` (narrowed copy, only the given tools on, MCP servers kept only for given MCP tools with the
  other tools of those servers off, built-in prompt `knowledge_agents/prompts/subagent.md`, no instructions, skills
  off, same model) and `referenced_credentials`. `agents.agent()` puts `context['mcp_tools']` (the MCP tools as the
  model sees them → `mcp:<server id>:<tool>`) in the run context.
- **Running (Task 4):** `subagents.Subagents` (one per native turn, created by `NativeRunner.run_turn`, passed to
  `agent(..., subagents=)` into the run context) runs each accepted call through `agents.agent` with the parent's
  scratch workspace, `SubagentEvents` hooks (the parent's recorder, every payload with `parent_call_id`, usage through
  the session's `add_usage`), an `asyncio.Semaphore(AGENT_MAX_SUBAGENTS)`, a 16-per-turn cap and a model-input filter
  that refuses once the parent's events are closed. The tool adapter publishes the call id in the context variable
  `capabilities.context.TOOL_CALL_ID`.
- **admin-ui (Tasks 5–6):** General switch, Tools tab **Agents** group (locked `run_subagent` row with usage and
  Reset, **A subagent may use** checklist), wizard switch and checklist; `groupTurns` gives each turn its
  `subagents`; `RunWorkspace` shows a subagent line with **Open** in the Worked block, every subagent in **Agents in
  this run**, and the selected subagent's own history with a breadcrumb.

**Tech Stack:**
- FastAPI, pydantic v2, psycopg3, OpenAI Agents SDK `openai-agents==0.19.2` (one response's function calls run
  concurrently; `RunRecorder.add()` is synchronous on the event loop), pytest against the disposable PostgreSQL fixture;
- React Router 7 with shadcn/ui, Vitest with Testing Library (`services/admin-ui`).

**Spec:** `docs/superpowers/specs/2026-10-09-subagents-design.md` (binding).

## Global Constraints

- Implementers and reviewers never run make targets, never read or modify `.env` or anything under `.local`, never
  touch the running Docker stack or its database, and NEVER `git stash`. Tests use the disposable PostgreSQL fixture
  (`services/admin-api/tests/conftest.py`) and `db/tests/run.sh`, which start their own throwaway containers.
- Stage files explicitly (`git add <paths>`; never `git add -A` or `git add .`). Commit trailer:
  `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Credentials are never logged, stored or streamed unredacted: a subagent gets only the parent turn's already
  decrypted credentials its sub-config references (nothing new is decrypted), its events go through the parent's
  `RunRecorder` (redacted with the parent's secrets), and its answer and failure message are redacted with the
  parent's secrets before they become the tool result.
- No migration and no new column: the switch is the `run_subagent` row (0078's `subagents_do_not_spawn` check
  requires `subagent_allowed = false` for it).
- Native agents only; Claude Code agents are unaffected (no switch, no tool changes). Existing agents' tool rows are
  not changed by this step.
- Python stays compatible with 3.12. TypeScript passes `npx tsc --noEmit` (tests are type-checked too).
- Test commands (each task runs the whole suite of every service it touches before its commit; Task 7 runs all four):
  - `bash db/tests/run.sh`
  - `cd services/admin-api && uv run --frozen pytest -q`
  - `cd services/admin-ui && npx vitest run` and `cd services/admin-ui && npx tsc --noEmit`
  - `python3 -m unittest discover -s tests` (repository root)
- Known failures that are not regressions (leave them, mention them in the task report): `test_environments.py`
  rollback test, and the order-dependent docs-folder test in `test_deploy.py` (passes alone).
- Limits: `run_subagent` task 1–20,000 characters; at most **16** subagents per turn; `AGENT_MAX_SUBAGENTS` (default
  **4**) run at once per turn, later calls wait.
- Texts (API):
  - `tools_api.SUBAGENTS_OFF = 'Turn on May start subagents in General first.'` (400, tools PUT);
  - `tools_api.SUBAGENT_SWITCH = 'run_subagent comes with May start subagents.'` (400, wizard `tools`);
  - switch on a Claude Code agent: 400 `tools_api.CLAUDE_CODE` (today's native-only message for tools);
  - `subagents.UNAVAILABLE = 'Subagents run only in agent sessions.'`;
  - `subagents.EMPTY_TASK = 'Give the subagent a task.'`, `LONG_TASK = 'A subagent task is at most 20,000 characters.'`,
    `NO_TOOLS = 'Give the subagent at least one tool.'`, `NO_SPAWN = 'Tool run_subagent cannot be given to a subagent.'`,
    `NOT_OFFERED = 'Tool {name} is not available in this run.'`, `NOT_FOR_SUBAGENTS = 'Tool {name} may not be given to
    a subagent.'`, `NOT_GIVEN = '{label} {id} is not one of your resources.'`, `LIMIT = 'This turn already started 16
    subagents.'`, `FAILED = 'Subagent failed: {message}'`.
- Texts (UI): General `May start subagents` with `The agent can hand separate parts of its work to helpers that run in
  parallel.`; Tools tab group `Agents`, row label `Start subagents`, checklist `A subagent may use`, note `Turn on May
  start subagents in General to let this agent start helpers.`; Worked block line `Subagent · <task> · <n> tool
  call(s) · <tokens> tokens · <working|done|failed|stopped>` with **Open**; sidebar entry `<task>` / `<Working|Done|
  Failed|Stopped> · <n> tool call(s) · <tokens> tokens`; breadcrumb `Main agent › Subagent: <task>`.
- Default usage text of `run_subagent` (the spec's, verbatim): `Start a helper for a separate, well-defined part of
  your work, such as exploring one project or checking one question. Give it a complete task, because it does not see
  this conversation. Give it the fewest tools and resources it needs. Helpers may run in parallel. You get their final
  answer as the result; verify important claims.`
- Subagent prompt (`knowledge_agents/prompts/subagent.md`, verbatim): `You are a helper started by another agent for
  one task. Work only on that task with the tools and resources you were given. Read before you conclude; cite evidence
  as file:line, document path or command. If the task cannot be done with what you have, say what is missing. Finish
  with a concise answer for the agent that started you.`

## Review Focus

1. **A credential reaches a subagent's record or result.** Examples: a key in a subagent's tool output, final answer
   or failure message; a subagent given credentials its configuration does not reference. Pinned in:
   - Task 3: `test_a_subagent_gets_only_the_credentials_its_configuration_references`;
   - Task 4: `test_a_failed_subagent_returns_a_redacted_failure_and_is_recorded`,
     `test_a_session_turn_records_its_subagents_in_one_record_and_counts_their_usage` (`KEY` absent from the record).
2. **A subagent outlives Stop or starts work in a closed turn.** Examples: Stop leaves a subagent's tool running; a
   subagent of a stopped turn makes another model call or tool call; a `run_subagent` call after the turn closed
   starts a subagent. Pinned in:
   - Task 4: `test_stop_cancels_a_running_subagent_and_leaves_nothing_running`,
     `test_a_closed_turn_starts_no_subagent_model_call_tool_call_or_subagent`.
3. **Parallel calls break the shared record or the limits.** Examples: two subagents' events interleaving with
   duplicate or missing `seq`; a 17th subagent slipping through when calls arrive together; a fifth subagent running
   while four run; a refused call counting towards 16. Pinned in:
   - Task 4: `test_at_most_the_limit_run_at_once_and_later_ones_wait`,
     `test_a_turn_starts_at_most_16_subagents_and_refused_calls_do_not_count`,
     `test_a_session_turn_records_its_subagents_in_one_record_and_counts_their_usage` (`seq` 1..n).
4. **A save quietly changes the switch or what subagents may use.** Examples: a Tools save without `run_subagent`
   deleting it or its usage; a Tools save while the switch is off adding it; a profile save from a client that does not
   send `subagents` switching it off; a profile save that switches no capability on turning tools back on (existing
   agents changed). Pinned in:
   - Task 1: `test_a_save_that_switches_nothing_on_leaves_the_tools_alone`;
   - Task 2: `test_the_general_switch_is_the_run_subagent_row`,
     `test_the_tools_tab_keeps_run_subagent_on_and_stores_subagent_allowed`.
5. **A subagent reaches more than its parent may.** Examples: a resource outside a per-run parent's narrowed lists; a
   kind the parent does not use; an MCP tool of a kept server that was not given; `run_subagent` or a tool marked not
   for subagents given anyway; the Tools tab name `mcp:<id>:<tool>` accepted instead of the name the model sees.
   Pinned in:
   - Task 3: `test_a_subagent_gets_only_the_given_tools_and_resources_and_its_own_prompt`,
     `test_checks_refuse_with_a_message_naming_the_tool_or_resource`.

The workspace grouping (a page that starts in the middle of a subagent, a turn stopped while a subagent works) is
pinned in Task 6: `a subagent works until its result arrives, is stopped when the turn ends first, and may start
before the page`.

## Decisions (where the spec leaves a choice or the code needs one)

- **The call id reaches the tool through a context variable.** Catalog tools get only the run context (`RunContext`),
  which every concurrent call shares. `tools/sdk.function_tool` sets `capabilities.context.TOOL_CALL_ID` to
  `ToolContext.tool_call_id` around each invocation; `run_subagent` reads it synchronously in the same coroutine.
- **MCP tools "as the model sees them"** are the SDK's server-prefixed names (`include_server_in_tool_names`, e.g.
  `mcp_<server id>__lookup`, hashed when long). `agents.agent()` builds `context['mcp_tools']` by pairing
  `Agent.get_mcp_tools()` with each server's `list_tools()` (both public, cached, same order); a length mismatch gives
  `{}` (no MCP tool can be given rather than a wrong one).
- **A narrowed subagent is a per-run run.** When `resources` is given, the sub-config gets `resources = 'per_run'` and
  `run_resources = {kind: ids}`, so an empty list means nothing of that kind (the existing `require_given` rule) and
  the prompt says the run got only the listed resources. With `resources = None` the parent's lists and mode are kept.
- **Subagent events:** the first is `input {task, subagent: {task}, parent_call_id}`; then `instructions` (the
  subagent prompt), `reasoning`, `message`, `tool_call`, `tool_result`, `usage`, `error` as for the main agent, each
  with `parent_call_id`. There is no `start` or `end` event for a subagent (an `end` would end the turn in
  `groupTurns`).
- **A failed subagent's result is the plain text `Subagent failed: <message>`** (the message as `native_sessions.
  failure` writes it, redacted). `agent_activity.tool_failed` also recognises that prefix, so the `tool_result` event
  has `is_error = true` and the workspace shows the subagent as failed. Refusals (the checks) raise `ValueError`, which
  the adapter returns as its usual error text (also `is_error`).
- **The switch keeps its row while an agent is Claude Code.** `GET /api/agents/{id}` reports `subagents` only for
  native agents; switching to Claude Code and back keeps the row and its usage. `subagents: true` for a Claude Code
  agent is refused; `subagents` omitted (`None`) keeps the switch.
- **The wizard** gets `subagents: bool = False` and `subagent_blocked: list[str]` (tools a subagent may not use);
  `tools` may not contain `run_subagent`.
- **Turn usage and tool calls in the workspace** stay the main agent's own (a subagent's model calls are on its line);
  the header and the sidebar's main agent line keep the session totals, which include subagents.
- **A subagent's status** is `working` until the parent's `run_subagent` result, then `done` or `failed`
  (`is_error`); when the turn ends first it is `stopped`.
- **The sub-config's name** is `<agent name> · subagent` (the `instructions` event and traces say which agent spoke).

## Delivery and test boundaries

Tasks land in order on `feature/subagents`; every suite of the touched services is green after each task. Task 1
(tool defaults) stands alone. Task 2 (switch, catalog entry, `subagent_allowed`) needs nothing from Task 1 but follows
it. Task 3 (pure checks and sub-config, MCP names in the run context) needs Task 2's catalog entry and
`subagent_allowed` in the configuration. Task 4 (running) needs Task 3. Task 5 (UI configuration) needs Task 2's API
shape. Task 6 (workspace) needs Task 4's event shape. Task 7 updates the docs and runs every suite.

## File Structure

| File | Responsibility | Task |
| --- | --- | --- |
| `services/admin-api/src/knowledge_admin/tools/catalog.py` | `capability_tools`, `enabled_capability_tools` (1); `run_subagent` entry, `SubagentResources`, `RunSubagent`, `SUBAGENT`, `item(subagent_allowed=)` (2) | 1, 2 |
| `services/admin-api/src/knowledge_admin/tools_api.py` | `switch_on_defaults` (1); switch helpers, PUT rules, `subagent_allowed` stored (2) | 1, 2 |
| `services/admin-api/src/knowledge_admin/profiles_api.py` | resources in the defaults rule (1); `subagents` (2) | 1, 2 |
| `services/admin-api/src/knowledge_admin/agent_wizard.py` | `enabled_capability_tools` (1); `subagents`, `subagent_blocked` (2) | 1, 2 |
| `services/admin-api/src/knowledge_admin/knowledge_agents/config.py` | `subagent_allowed` in tool items and sections (2); `mcp_definitions`, `credential_references` (3) | 2, 3 |
| `services/admin-api/src/knowledge_admin/knowledge_agents/subagents.py` | `run_subagent` (2); checks, sub-config, credentials (3); `SubagentEvents`, `Subagents` (4) | 2, 3, 4 |
| `services/admin-api/src/knowledge_admin/knowledge_agents/prompts/subagent.md` | the subagent prompt | 3 |
| `services/admin-api/src/knowledge_admin/agents.py` | `load_credentials` on `credential_references`, `mcp_tool_names`, `context['mcp_tools']` (3); `workspace`, `subagents` (4) | 3, 4 |
| `services/admin-api/src/knowledge_admin/capabilities/context.py` | `RunContext.mcp_tools` (3); `subagents`, `TOOL_CALL_ID` (4) | 3, 4 |
| `services/admin-api/src/knowledge_admin/tools/sdk.py` | sets `TOOL_CALL_ID` | 4 |
| `services/admin-api/src/knowledge_admin/agent_activity.py` | `tool_failed` knows `Subagent failed: ` | 4 |
| `services/admin-api/src/knowledge_admin/knowledge_agents/native_sessions.py` | `SessionEvents.add`, `NativeRunner(run_subagent=)`, a `Subagents` per turn | 4 |
| `deploy/compose.yaml`, `tests/test_setup.py` | `AGENT_MAX_SUBAGENTS` for agent-session-worker | 4 |
| `services/admin-api/tests/native_fakes.py` | `FakeAgent(subagents=)` and `Turn.context`, `FakeSubagent`, `delegate`, `together` | 4 |
| `services/admin-api/tests/test_subagent_switch.py` | switch, Tools rules, wizard, clone | 2 |
| `services/admin-api/tests/test_subagent_config.py` | checks, sub-config, credentials, MCP names | 3 |
| `services/admin-api/tests/test_subagents.py` | `Subagents` and the worker | 4 |
| `services/admin-ui/app/lib/{profiles,tools,agentWizard}.ts` | `subagents`, `subagent_allowed`, texts, `impliedTools` without agents | 5 |
| `services/admin-ui/app/components/{AgentProfileSettings,AgentToolsView,AgentWizard}.tsx` | switch, Agents group, wizard | 5 |
| `services/admin-ui/app/lib/turns.ts` | `Subagent`, `groupTurns` subagents | 6 |
| `services/admin-ui/app/components/RunWorkspace.tsx` | subagent line, sidebar entries, selected subagent | 6 |
| `docs/agents.md`, `docs/agent-profiles.md`, `docs/user-guide.md`, `services/admin-api/src/knowledge_admin/tools/README.md`, `deploy/README.md` | documentation | 7 |

---

### Task 1: Tool defaults — a capability's tools, all of them

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/tools/catalog.py` (replace `capability_defaults`, lines 361–366)
- Modify: `services/admin-api/src/knowledge_admin/tools_api.py` (imports, `PRIMARY`, `flags`, `switch_on_defaults`)
- Modify: `services/admin-api/src/knowledge_admin/profiles_api.py:261-266`
- Modify: `services/admin-api/src/knowledge_admin/agent_wizard.py` (imports, `create` lines 275–282)
- Test: `services/admin-api/tests/test_tool_catalog.py`, `services/admin-api/tests/test_agent_tools_api.py`

**Interfaces:**
- Produces: `catalog.CAPABILITY_NEEDS: dict[str, tuple[str, ...]]`,
  `catalog.capability_tools(capability: str, config: Mapping[str, Any]) -> list[str]`,
  `catalog.enabled_capability_tools(config: Mapping[str, Any]) -> list[str]` (catalog order);
  `tools_api.flags(row)` gains `'resources'` (`row.get('resources', 'fixed')`);
  `tools_api.switch_on_defaults(conn, agent_id, old, new)` (same signature; `old`/`new` are `flags(...)` shapes).
- Removed: `catalog.capability_defaults`, `tools_api.PRIMARY`.

- [ ] **Step 1: Write the failing catalog test**

Append to `services/admin-api/tests/test_tool_catalog.py` (and add `capability_tools, enabled_capability_tools` to its
`from knowledge_admin.tools.catalog import ...` line):

```python
CODE_TOOLS = ['list_files', 'read_file', 'search_files', 'shell_run', 'code_search', 'ast_grep', 'code_graph']
FILE_TOOLS = ['list_files', 'read_file', 'search_files', 'shell_run']


def test_a_capabilitys_tools_are_every_tool_of_its_needs():
    assert capability_tools('code', config(code=True)) == CODE_TOOLS  # code_graph too, graph or not
    assert capability_tools('documents', config(documents=True)) == [*FILE_TOOLS, 'search_documents', 'read_document']
    # with code projects on the file tools belong to code; report_not_covered only while grounded
    assert capability_tools('documents', config(code=True, documents=True, grounded=True)) == [
        'search_documents', 'read_document', 'report_not_covered']
    # all of them, not only those default_on gives: grounded documents keep the file tools' rows
    assert capability_tools('documents', config(documents=True, grounded=True)) == [
        *FILE_TOOLS, 'search_documents', 'read_document', 'report_not_covered']
    assert capability_tools('ssh', config(ssh=True)) == ['ssh_run']
    assert enabled_capability_tools(config(code=True, ssh=True)) == [*CODE_TOOLS, 'ssh_run']
    assert enabled_capability_tools(config(code=True, documents=True)) == [*CODE_TOOLS, 'search_documents',
                                                                          'read_document']  # each once
    assert enabled_capability_tools(config()) == []
```

- [ ] **Step 2: Write the failing API tests**

In `services/admin-api/tests/test_agent_tools_api.py`, add after `rows`:

```python
CODE_TOOLS = {'list_files', 'read_file', 'search_files', 'shell_run', 'code_search', 'ast_grep', 'code_graph'}


def usage_of(store, agent_id, tool):
    with store.connect() as conn:
        return conn.execute('SELECT usage FROM ka.agent_tools WHERE agent_id=%s AND tool=%s', (agent_id, tool)).fetchone()['usage']


def profile_of(client, agent_id):
    return client.get(f'/api/agents/{agent_id}', headers=HEADERS).json()
```

Replace `test_switching_a_capability_on_switches_its_default_tools_on` with:

```python
def test_switching_a_capability_on_switches_all_its_tools_on(store, saved_agent):
    client = saved_agent[0]
    blank = create(client, 'Blank')
    assert rows(store, blank['id']) == set()
    code = update(client, blank, repositories_enabled=True).json()
    assert rows(store, blank['id']) == CODE_TOOLS
    only = put(client, blank['id'], code['revision'], [
        {'tool': 'read_file', 'mode': 'on', 'usage': 'Read narrowly.'},
        *({'tool': name, 'mode': 'off', 'usage': None} for name in sorted(CODE_TOOLS - {'read_file'}))]).json()
    profile = profile_of(client, blank['id'])
    assert profile['revision'] == only['revision']
    documents = update(client, profile, documents_enabled=True).json()
    assert rows(store, blank['id']) == {'read_file', 'search_documents', 'read_document'}  # code keeps its choices
    grounded = update(client, documents, documents_grounded=True).json()
    assert 'report_not_covered' in rows(store, blank['id'])
    ssh = update(client, grounded, ssh_enabled=True).json()
    assert 'ssh_run' in rows(store, blank['id'])
    off = update(client, ssh, repositories_enabled=False).json()
    assert 'read_file' in rows(store, blank['id']) and 'shell_run' not in rows(store, blank['id'])  # off keeps rows
    update(client, off, repositories_enabled=True)  # on again: all of its tools, replacing the off rows
    assert rows(store, blank['id']) >= CODE_TOOLS
    assert usage_of(store, blank['id'], 'read_file') == 'Read narrowly.'  # switching on keeps edited usage


def test_a_per_run_agent_gets_all_tools_of_its_enabled_capabilities(store, saved_agent):
    client = saved_agent[0]
    role = client.post('/api/agents/wizard', headers=HEADERS, json=wizard_payload(
        'Role', resources='per_run', repositories_enabled=True, documents_enabled=True)).json()
    assert rows(store, role['id']) == CODE_TOOLS | {'search_documents', 'read_document'}
    profile = profile_of(client, role['id'])
    trimmed = put(client, role['id'], profile['revision'], [
        {'tool': name, 'mode': 'off' if name == 'shell_run' else 'on', 'usage': None}
        for name in sorted(CODE_TOOLS | {'search_documents', 'read_document'})]).json()
    assert 'shell_run' not in rows(store, role['id'])
    ssh = update(client, profile_of(client, role['id']), ssh_enabled=True).json()  # a capability on while per run
    assert rows(store, role['id']) == CODE_TOOLS | {'search_documents', 'read_document', 'ssh_run'}
    assert ssh['revision'] == trimmed['revision'] + 1
    fixed = create(client, 'Fixed first')
    coded = update(client, fixed, repositories_enabled=True).json()
    put(client, fixed['id'], coded['revision'], [{'tool': name, 'mode': 'off' if name == 'code_search' else 'on',
                                                  'usage': None} for name in sorted(CODE_TOOLS)])
    update(client, profile_of(client, fixed['id']), resources='per_run')  # switched to per run
    assert rows(store, fixed['id']) == CODE_TOOLS


def test_a_save_that_switches_nothing_on_leaves_the_tools_alone(store, saved_agent):
    """Existing agents are not changed: only switching a capability on (or to per run) switches tools on."""
    client = saved_agent[0]
    for resources in ('fixed', 'per_run'):
        agent = client.post('/api/agents/wizard', headers=HEADERS, json=wizard_payload(
            f'Kept {resources}', resources=resources, repositories_enabled=True)).json()
        profile = profile_of(client, agent['id'])
        put(client, agent['id'], profile['revision'], [{'tool': 'read_file', 'mode': 'off', 'usage': None}])
        update(client, profile_of(client, agent['id']), name=f'Renamed {resources}')
        assert rows(store, agent['id']) == set(), resources
```

- [ ] **Step 3: Run them to verify they fail**

Run: `cd services/admin-api && uv run --frozen pytest -q tests/test_tool_catalog.py tests/test_agent_tools_api.py`
Expected: FAIL — `ImportError: cannot import name 'capability_tools'`, and (after it exists) the per-run and
"all its tools" assertions.

- [ ] **Step 4: Implement the catalog rule**

In `services/admin-api/src/knowledge_admin/tools/catalog.py` replace `capability_defaults` (lines 361–366) with:

```python
CAPABILITY_NEEDS: dict[str, tuple[str, ...]] = {
    'code': ('files', 'code', 'code_graph'), 'documents': ('documents', 'grounded'), 'ssh': ('ssh',)}


def capability_tools(capability: str, config: Mapping[str, Any]) -> list[str]:
    """All tools of one capability, by need, in catalog order (not only those default_on gives). With code projects
    off, documents also bring the file tools. report_not_covered belongs to "Answer only from documents" and is listed
    only while it is on."""
    needs = CAPABILITY_NEEDS[capability] + (('files',) if capability == 'documents' and not _code(config) else ())
    return [spec.name for spec in CATALOG.values()
            if spec.needs in needs and (spec.name != 'report_not_covered' or _grounded(config))]


def enabled_capability_tools(config: Mapping[str, Any]) -> list[str]:
    """The tools of every capability switched on, each once, in catalog order."""
    names = {name for capability in CAPABILITY_NEEDS if config[capability]['enabled']
             for name in capability_tools(capability, config)}
    return [name for name in CATALOG if name in names]
```

- [ ] **Step 5: Implement the save rule**

In `services/admin-api/src/knowledge_admin/tools_api.py`:
- import line becomes `from knowledge_admin.tools.catalog import (CATALOG, NEED_REASONS, NEED_TABS, capability_tools,
  catalog_listing, enabled_capability_tools, implied_tools, needs_met)`;
- delete the `PRIMARY` comment and constant;
- `flags` returns `'resources'` too:

```python
def flags(row: dict[str, Any]) -> dict[str, Any]:
    """The capability switches of an agent row (or a proposal) in config shape, for the catalog rules."""
    return {'runtime': row['runtime'], 'resources': row.get('resources', 'fixed'),
            'code': {'enabled': row['repositories_enabled']},
            'documents': {'enabled': row['documents_enabled'], 'grounded': row['documents_grounded']},
            'ssh': {'enabled': row['ssh_enabled']}}
```

- replace `switch_on_defaults` with:

```python
CAPABILITIES = ('code', 'documents', 'ssh')


def switch_on_defaults(conn, agent_id: str, old: dict[str, Any], new: dict[str, Any]) -> None:
    """Profile save. Fixed agents: a capability switched on gets all of its tools on (catalog.capability_tools),
    replacing earlier off rows (they had no effect while it was off). Per-run agents: when the agent becomes per run or
    a capability is switched on, every enabled capability's tools are switched on. Grounded answers always get
    report_not_covered. Switching a capability off, and a save that switches nothing on, keep every row; edited usage
    text is kept."""
    if new['runtime'] != 'openai':
        return
    switched_on = [capability for capability in CAPABILITIES
                   if new[capability]['enabled'] and not old[capability]['enabled']]
    wanted: set[str] = set()
    if new['resources'] == 'per_run' and (old['resources'] != 'per_run' or switched_on):
        wanted.update(enabled_capability_tools(new))
    for capability in switched_on:
        wanted.update(capability_tools(capability, new))
    if new['documents']['enabled'] and new['documents']['grounded']:
        wanted.add('report_not_covered')
    for name in sorted(wanted):
        conn.execute(SWITCH_ON, (agent_id, name))
```

In `services/admin-api/src/knowledge_admin/profiles_api.py` (update, lines 261–263) pass the resources mode:

```python
                now = flags({'runtime': runtime, 'resources': resources,
                             'repositories_enabled': value.repositories_enabled,
                             'documents_enabled': value.documents_enabled, 'documents_grounded': grounded,
                             'ssh_enabled': value.ssh_enabled})
```

(`flags(old)` already gets `old['resources']` from the profile row.)

In `services/admin-api/src/knowledge_admin/agent_wizard.py` import `enabled_capability_tools` instead of
`implied_tools` from the catalog and replace lines 276–280:

```python
                    proposed = flags({'runtime': 'openai', 'resources': value.resources,
                                      'repositories_enabled': value.repositories_enabled,
                                      'documents_enabled': value.documents_enabled, 'documents_grounded': False,
                                      'ssh_enabled': value.ssh_enabled})
                    names = value.tools if value.tools is not None else enabled_capability_tools(proposed)
```

- [ ] **Step 6: Run the tests**

Run: `cd services/admin-api && uv run --frozen pytest -q tests/test_tool_catalog.py tests/test_agent_tools_api.py`
Expected: PASS.

Run: `cd services/admin-api && uv run --frozen pytest -q`
Expected: PASS except the known failures.

- [ ] **Step 7: Commit**

```bash
git add services/admin-api/src/knowledge_admin/tools/catalog.py services/admin-api/src/knowledge_admin/tools_api.py \
  services/admin-api/src/knowledge_admin/profiles_api.py services/admin-api/src/knowledge_admin/agent_wizard.py \
  services/admin-api/tests/test_tool_catalog.py services/admin-api/tests/test_agent_tools_api.py
git commit -m "feat(admin-api): switching a capability on, or a per-run agent, gets all of its tools

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: May start subagents — the `run_subagent` row, the catalog entry and `subagent_allowed`

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/tools/catalog.py` (arguments, usage text, entry, `item`)
- Create: `services/admin-api/src/knowledge_admin/knowledge_agents/subagents.py`
- Modify: `services/admin-api/src/knowledge_admin/knowledge_agents/config.py` (`tool_items`, `native_tools`, `sections`)
- Modify: `services/admin-api/src/knowledge_admin/tools_api.py` (`ToolChoice`, `agent_tools`, `save`, switch helpers)
- Modify: `services/admin-api/src/knowledge_admin/profiles_api.py` (`ProfileInput`, `read`, `update`)
- Modify: `services/admin-api/src/knowledge_admin/agent_wizard.py` (`CreateInput`, `create`)
- Create: `services/admin-api/tests/test_subagent_switch.py`
- Modify tests: `services/admin-api/tests/test_agent_tools_api.py` (catalog listing, remove the "ignored" test),
  `services/admin-api/tests/test_tool_catalog.py`

**Interfaces:**
- Produces (catalog): `SUBAGENT = 'run_subagent'`, `RUN_SUBAGENT` (usage text), `SubagentResources`, `RunSubagent`
  (`task: str`, `tools: list[str]`, `resources: SubagentResources | None`), `CATALOG['run_subagent']` (group `agents`,
  needs `None`, `default_on` always false), `item(name, mode, usage=None, group=None, subagent_allowed=True)`.
- Produces (config): every tool item carries the row's `subagent_allowed` (no row: `True`);
  `sections(config)['tools']` entries include `subagent_allowed`.
- Produces (tools_api): `SUBAGENTS_OFF`, `SUBAGENT_SWITCH`, `subagents_on(conn, agent_id) -> bool`,
  `switch_subagents(conn, agent_id, on: bool) -> None`.
- Produces (API): `GET /api/agents/{id}` → `subagents: bool`; `PUT /api/agents/{id}` accepts `subagents: bool | None`;
  `PUT /api/agents/{id}/tools` stores `subagent_allowed`; `POST /api/agents/wizard` accepts `subagents`,
  `subagent_blocked`.
- Produces (subagents): `UNAVAILABLE`, `async run_subagent(context, task, tools, resources) -> str` (delegates to
  `context['subagents'].run(context, task, tools, resources)`; Task 4 adds the call id).

- [ ] **Step 1: Write the failing tests**

Create `services/admin-api/tests/test_subagent_switch.py`:

```python
"""May start subagents: the run_subagent row behind the General switch, the Tools tab's rules for it and for
subagent_allowed, the wizard and clones."""
import asyncio
import json

from agents.tool_context import ToolContext

from knowledge_admin.knowledge_agents.config import get_agent_config
from knowledge_admin.knowledge_agents.subagents import UNAVAILABLE
from knowledge_admin.profiles_api import ProfileInput
from knowledge_admin.tools.catalog import CATALOG, SUBAGENT
from knowledge_admin.tools.sdk import function_tool
from knowledge_admin.tools_api import CLAUDE_CODE, SUBAGENT_SWITCH, SUBAGENTS_OFF, UNKNOWN
from test_agent_tools_api import put, view
from test_agents import agent_mounts, saved_agent  # noqa: F401  (fixtures)
from test_claude_code_agents import claude_code_agent
from test_profiles import create, update, wizard_payload
from test_repositories import HEADERS


def profile(client, agent_id):
    return client.get(f'/api/agents/{agent_id}', headers=HEADERS).json()


def row(store, agent_id, tool=SUBAGENT):
    with store.connect() as conn:
        return conn.execute('SELECT mode,usage,subagent_allowed FROM ka.agent_tools WHERE agent_id=%s AND tool=%s',
                            (agent_id, tool)).fetchone()


def test_the_general_switch_is_the_run_subagent_row(store, saved_agent):
    client = saved_agent[0]
    agent = create(client, 'Lead')
    assert agent['subagents'] is False and row(store, agent['id']) is None
    assert SUBAGENT not in [tool['tool'] for tool in view(client, agent['id'])['tools']]
    on = update(client, agent, subagents=True)
    assert on.status_code == 200, on.text
    assert on.json()['subagents'] is True and on.json()['revision'] == agent['revision'] + 1
    assert row(store, agent['id']) == {'mode': 'on', 'usage': None, 'subagent_allowed': False}
    listed = {tool['tool']: tool for tool in view(client, agent['id'])['tools']}
    assert (listed[SUBAGENT]['group'], listed[SUBAGENT]['label'], listed[SUBAGENT]['mode']) == ('agents', 'Start subagents', 'on')
    assert listed[SUBAGENT]['default_usage'] == CATALOG[SUBAGENT].usage
    items = {entry['tool']: entry for entry in get_agent_config(agent['id'], conninfo=store.conninfo)['tools']['items']}
    assert (items[SUBAGENT]['mode'], items[SUBAGENT]['subagent_allowed']) == ('on', False)
    current = on.json()
    body = {field: current[field] for field in ProfileInput.model_fields if field != 'subagents'}
    kept = client.put(f"/api/agents/{agent['id']}", headers=HEADERS, json={**body, 'name': 'Lead 2'})
    assert kept.status_code == 200 and kept.json()['subagents'] is True  # a client that does not send it keeps it
    off = update(client, kept.json(), subagents=False).json()
    assert off['subagents'] is False and row(store, agent['id']) is None
    writer = claude_code_agent(client)
    assert profile(client, writer)['subagents'] is False
    refused = update(client, profile(client, writer), subagents=True)
    assert refused.status_code == 400 and refused.json()['error']['message'] == CLAUDE_CODE


def test_the_tools_tab_keeps_run_subagent_on_and_stores_subagent_allowed(store, saved_agent):
    client, mcp = saved_agent[0], saved_agent[4]
    agent = create(client, 'Lead')
    refused = put(client, agent['id'], view(client, agent['id'])['revision'], [{'tool': SUBAGENT, 'mode': 'on', 'usage': None}])
    assert refused.status_code == 400 and refused.json()['error']['message'] == SUBAGENTS_OFF
    on = update(client, profile(client, agent['id']), subagents=True).json()
    saved = put(client, agent['id'], on['revision'], [
        {'tool': SUBAGENT, 'mode': 'off', 'usage': 'Split the work by project.'},
        {'tool': 'read_file', 'mode': 'on', 'usage': None, 'subagent_allowed': False},
        {'tool': 'list_files', 'mode': 'on', 'usage': None}])
    assert saved.status_code == 200, saved.text
    assert row(store, agent['id']) == {'mode': 'on', 'usage': 'Split the work by project.', 'subagent_allowed': False}
    assert row(store, agent['id'], 'read_file')['subagent_allowed'] is False
    assert row(store, agent['id'], 'list_files')['subagent_allowed'] is True
    tools = {tool['tool']: tool for tool in saved.json()['tools']}
    assert tools['read_file']['subagent_allowed'] is False and tools[SUBAGENT]['usage'] == 'Split the work by project.'
    items = {entry['tool']: entry for entry in get_agent_config(agent['id'], conninfo=store.conninfo)['tools']['items']}
    assert items['read_file']['subagent_allowed'] is False and items['list_files']['subagent_allowed'] is True
    again = put(client, agent['id'], saved.json()['revision'], [{'tool': 'read_file', 'mode': 'on', 'usage': None}])
    assert again.status_code == 200
    assert row(store, agent['id'])['usage'] == 'Split the work by project.'  # only the switch removes it
    lookup = f"mcp:{mcp['id']}:lookup"
    revision = view(client)['revision']
    hidden = put(client, 'operations', revision, [{'tool': lookup, 'mode': 'on', 'usage': None, 'subagent_allowed': False}])
    assert hidden.status_code == 200 and row(store, 'operations', lookup)['subagent_allowed'] is False
    put(client, 'operations', hidden.json()['revision'], [{'tool': lookup, 'mode': 'on', 'usage': None}])
    assert row(store, 'operations', lookup) is None  # on, default text, allowed: an MCP tool's default has no row


def test_the_wizard_switches_subagents_on_with_the_tools_a_subagent_may_use(store, saved_agent):
    client = saved_agent[0]
    created = client.post('/api/agents/wizard', headers=HEADERS, json=wizard_payload(
        'Lead', repositories_enabled=True, subagents=True, subagent_blocked=['shell_run']))
    assert created.status_code == 201, created.text
    agent_id = created.json()['id']
    assert row(store, agent_id) == {'mode': 'on', 'usage': None, 'subagent_allowed': False}
    assert row(store, agent_id, 'shell_run')['subagent_allowed'] is False
    assert row(store, agent_id, 'read_file')['subagent_allowed'] is True
    assert profile(client, agent_id)['subagents'] is True
    for body, message in ((wizard_payload('Odd', tools=[SUBAGENT]), SUBAGENT_SWITCH),
                          (wizard_payload('Odder', subagent_blocked=['grep']), UNKNOWN.format(tool='grep')),
                          (wizard_payload('Writer', description='', when_to_use='', runtime='claude_code',
                                          subagents=True), CLAUDE_CODE)):
        refused = client.post('/api/agents/wizard', headers=HEADERS, json=body)
        assert refused.status_code == 400 and refused.json()['error']['message'] == message


def test_a_clone_copies_the_switch_and_what_subagents_may_use(store, saved_agent):
    client = saved_agent[0]
    agent = create(client, 'Lead')
    on = update(client, agent, subagents=True, repositories_enabled=True).json()
    put(client, agent['id'], on['revision'], [{'tool': SUBAGENT, 'mode': 'on', 'usage': 'Split by project.'},
                                              {'tool': 'read_file', 'mode': 'on', 'usage': None, 'subagent_allowed': False}])
    copied = client.post(f"/api/agents/{agent['id']}/clone", headers=HEADERS)
    assert copied.status_code == 201, copied.text
    clone = copied.json()['id']
    assert profile(client, clone)['subagents'] is True
    assert row(store, clone) == {'mode': 'on', 'usage': 'Split by project.', 'subagent_allowed': False}
    assert row(store, clone, 'read_file')['subagent_allowed'] is False


def test_run_subagent_outside_a_session_is_a_tool_error():
    tool = function_tool(CATALOG[SUBAGENT], CATALOG[SUBAGENT].usage)
    context = {'config': {}, 'credentials': {}, 'workspace': None, 'shell_enabled': False,
               'tools': frozenset({SUBAGENT})}
    payload = json.dumps({'task': 'Read the worker', 'tools': ['read_file'], 'resources': None})
    wrapped = ToolContext(context=context, tool_name=SUBAGENT, tool_call_id='s1', tool_arguments=payload)
    result = asyncio.run(tool.on_invoke_tool(wrapped, payload))
    assert result.startswith('An error occurred while running the tool.') and UNAVAILABLE in result
```

In `services/admin-api/tests/test_agent_tools_api.py`:
- in `test_the_catalog_and_an_agents_tools` replace `assert names == [*CATALOG, f"mcp:{mcp['id']}:lookup"]` with
  `assert names == [*(name for name in CATALOG if name != 'run_subagent'), f"mcp:{mcp['id']}:lookup"]  # run_subagent: only with its switch`;
- delete `test_a_choice_may_carry_subagent_allowed_which_is_ignored` (replaced by the switch tests).

Append to `services/admin-api/tests/test_tool_catalog.py`:

```python
def test_run_subagent_is_never_on_by_default_and_never_implied():
    spec = CATALOG['run_subagent']
    assert (spec.group, spec.label, spec.needs) == ('agents', 'Start subagents', None)
    assert spec.usage.startswith('Start a helper for a separate, well-defined part of your work')
    every = config(code=True, documents=True, ssh=True)
    assert not spec.default_on(every)
    assert 'run_subagent' not in {entry['tool'] for entry in implied_tools(every)['items'] if entry['mode'] == 'on'}
    assert 'run_subagent' not in enabled_capability_tools(every)
    assert 'run_subagent' in offered(config(tools=['run_subagent']))  # offered exactly when its row is on
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd services/admin-api && uv run --frozen pytest -q tests/test_subagent_switch.py tests/test_agent_tools_api.py tests/test_tool_catalog.py`
Expected: FAIL — `ImportError` (`SUBAGENT`, `subagents` module, `SUBAGENTS_OFF`), then `KeyError: 'subagents'`.

- [ ] **Step 3: The catalog entry**

In `services/admin-api/src/knowledge_admin/tools/catalog.py`, after `class SshRun`:

```python
class SubagentResources(Arguments):
    code_project_ids: list[str] = Field(default_factory=list, description='Code project IDs from your resources.')
    collection_ids: list[str] = Field(default_factory=list, description='Document collection IDs from your resources.')
    server_ids: list[str] = Field(default_factory=list, description='SSH server IDs from your resources.')
    service_ids: list[str] = Field(default_factory=list, description='Service IDs from your resources.')


class RunSubagent(Arguments):
    task: str = Field(description='The complete task, up to 20000 characters: the helper does not see this conversation.')
    tools: list[str] = Field(description='Names of your tools the helper may use, as you see them; at least one.')
    resources: SubagentResources | None = Field(
        None, description='The resources the helper may use, from yours; omit it to give all of yours.')
```

After `SSH_RUN`:

```python
RUN_SUBAGENT = ('Start a helper for a separate, well-defined part of your work, such as exploring one project or '
                'checking one question. Give it a complete task, because it does not see this conversation. Give it '
                'the fewest tools and resources it needs. Helpers may run in parallel. You get their final answer as '
                'the result; verify important claims.')
SUBAGENT = 'run_subagent'


def _never(config: Mapping[str, Any]) -> bool:
    return False  # offered exactly when its row is on: the switch May start subagents


async def _run_subagent(context: RunContext, arguments: BaseModel) -> str:
    # Imported here: the subagent runtime imports the native session runtime, which imports this module.
    from knowledge_admin.knowledge_agents import subagents
    return await subagents.run_subagent(context, **arguments.model_dump())
```

Add the entry as the last element of `CATALOG`:

```python
    ToolSpec(SUBAGENT, 'agents', 'Start subagents', 'Hand a separate part of the work to a helper that runs in parallel.',
             RUN_SUBAGENT, RunSubagent, _run_subagent, None, _never),
```

Replace `item`:

```python
def item(name: str, mode: str, usage: str | None = None, group: str | None = None,
         subagent_allowed: bool = True) -> dict[str, Any]:
    return {'tool': name, 'group': group or CATALOG[name].group, 'mode': mode, 'usage': usage,
            'subagent_allowed': subagent_allowed}
```

Create `services/admin-api/src/knowledge_admin/knowledge_agents/subagents.py`:

```python
"""run_subagent: a native agent hands a separate part of its work to a helper (a subagent) that runs in parallel with
the tools and resources it is given and without the conversation. Subagents run only in native agent sessions (Test
and Generate documents), whose runner puts the turn's Subagents into the run context."""
from typing import Any

UNAVAILABLE = 'Subagents run only in agent sessions.'


async def run_subagent(context: Any, task: str, tools: list[str], resources: dict[str, list[str]] | None) -> str:
    """The catalog tool: the subagent's final answer, or a ValueError the adapter returns as a tool error."""
    host = context.get('subagents')
    if host is None:
        raise ValueError(UNAVAILABLE)
    return await host.run(context, task, tools, resources)
```

- [ ] **Step 4: `subagent_allowed` in the configuration**

In `services/admin-api/src/knowledge_admin/knowledge_agents/config.py` replace `tool_items` and `native_tools`:

```python
def tool_items(rows: Iterable[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """The agent's tools in config shape: every catalog tool (no row: off), then MCP rows by name; each with whether
    run_subagent may give it to a subagent (no row, or a row read without it: yes)."""
    from knowledge_admin.tools.catalog import CATALOG, item  # the catalog imports this module

    saved = {row['tool']: row for row in rows}
    items = []
    for name in CATALOG:
        row = saved.get(name)
        items.append(item(name, row['mode'] if row else 'off', row['usage'] if row else None,
                          subagent_allowed=row.get('subagent_allowed', True) if row else True))
    for name in sorted(tool for tool in saved if tool.startswith('mcp:')):
        row = saved[name]
        items.append(item(name, row['mode'], row['usage'], group='mcp',
                          subagent_allowed=row.get('subagent_allowed', True)))
    return {'items': items}


def native_tools(conn, agent_id: str, runtime: str) -> dict[str, list[dict[str, Any]]]:
    if runtime != 'openai':
        return {'items': []}
    return tool_items(conn.execute('SELECT tool,mode,usage,subagent_allowed FROM ka.agent_tools WHERE agent_id=%s',
                                   (agent_id,)))
```

In `sections`, replace the `'tools'` line:

```python
        'tools': [{'tool': entry['tool'], 'mode': entry['mode'], 'usage': entry['usage'],
                   'subagent_allowed': entry.get('subagent_allowed', True)} for entry in tools_of(config)['items']],
```

- [ ] **Step 5: The Tools tab rules**

In `services/admin-api/src/knowledge_admin/tools_api.py`:
- import `SUBAGENT` from the catalog;
- after `MCP_REASON` add:

```python
SUBAGENTS_OFF = 'Turn on May start subagents in General first.'
SUBAGENT_SWITCH = 'run_subagent comes with May start subagents.'
# May start subagents is this row: always on, never given to a subagent (0078's subagents_do_not_spawn).
SUBAGENT_ROW = '''INSERT INTO ka.agent_tools(agent_id,tool,mode,usage,subagent_allowed) VALUES (%s,'run_subagent','on',%s,false)
    ON CONFLICT (agent_id,tool) DO UPDATE SET mode='on',subagent_allowed=false,updated_at=now()'''
INSERT_TOOL = 'INSERT INTO ka.agent_tools(agent_id,tool,mode,usage,subagent_allowed) VALUES (%s,%s,%s,%s,%s)'
```

- `ToolChoice.subagent_allowed` keeps `bool | None = None` with the comment `# None: a subagent may use it`;
- add after `_usage`:

```python
def subagents_on(conn, agent_id: str) -> bool:
    """May start subagents: whether the agent has the run_subagent row."""
    return conn.execute("SELECT 1 FROM ka.agent_tools WHERE agent_id=%s AND tool='run_subagent'",
                        (agent_id,)).fetchone() is not None


def switch_subagents(conn, agent_id: str, on: bool) -> None:
    """May start subagents on (the row, with the default usage text unless it has one) or off (no row)."""
    if on:
        conn.execute(SUBAGENT_ROW, (agent_id, None))
    else:
        conn.execute("DELETE FROM ka.agent_tools WHERE agent_id=%s AND tool='run_subagent'", (agent_id,))
```

- in `agent_tools`, first line of the catalog loop body after `row = saved.get(spec.name)`:

```python
        if spec.name == SUBAGENT and row is None:
            continue  # listed only while May start subagents is on
```

- in `save`, after `chosen = {choice.tool: choice for choice in value.tools}` add

```python
            switch = conn.execute("SELECT usage FROM ka.agent_tools WHERE agent_id=%s AND tool='run_subagent'",
                                  (agent_id,)).fetchone()
            if SUBAGENT in chosen and switch is None:
                raise HTTPException(400, SUBAGENTS_OFF)
```

  and replace the delete-and-insert loop (lines 175–181) with:

```python
            conn.execute('DELETE FROM ka.agent_tools WHERE agent_id=%s', (agent_id,))
            for choice in value.tools:
                usage = _usage(choice.usage)
                if choice.tool == SUBAGENT:  # on while the switch is on, whatever the choice says
                    conn.execute(SUBAGENT_ROW, (agent_id, usage))
                    continue
                allowed = choice.subagent_allowed is not False
                if choice.tool.startswith('mcp:') and choice.mode == 'on' and usage is None and allowed:
                    continue  # an MCP tool's default: no row
                conn.execute(INSERT_TOOL, (agent_id, choice.tool, choice.mode, usage, allowed))
            if switch is not None and SUBAGENT not in chosen:
                conn.execute(SUBAGENT_ROW, (agent_id, switch['usage']))  # only the switch removes it
```

- [ ] **Step 6: The profile and the wizard**

In `services/admin-api/src/knowledge_admin/profiles_api.py`:
- import `CLAUDE_CODE, subagents_on, switch_subagents` from `knowledge_admin.tools_api` (with the existing names);
- `ProfileInput` gains, after `resources`:

```python
    subagents: bool | None = Field(default=None, strict=True)  # May start subagents; None keeps the switch
```

- in `read`, before `return row`:

```python
        row['subagents'] = row['runtime'] == 'openai' and subagents_on(conn, agent_id)  # native agents only
```

- in `update`, after `resources = value.resources or old['resources']`:

```python
                if value.subagents and runtime != 'openai':
                    raise HTTPException(400, CLAUDE_CODE)
```

  and after `switch_on_defaults(conn, agent_id, flags(old), now)`:

```python
                if runtime == 'openai' and value.subagents is not None and value.subagents != subagents_on(conn, agent_id):
                    switch_subagents(conn, agent_id, value.subagents)  # the UPDATE above bumped the revision
```

In `services/admin-api/src/knowledge_admin/agent_wizard.py`:
- import `SUBAGENT` from the catalog and `SUBAGENT_SWITCH, switch_subagents` from `knowledge_admin.tools_api`;
- `CreateInput` gains:

```python
    subagents: bool = False  # May start subagents (native agents only)
    subagent_blocked: list[str] = Field(default_factory=list, max_length=50)  # tools a subagent may not be given
```

- in `create`, replace the Claude Code tools check and the unknown-tool loop with:

```python
                if value.runtime == 'claude_code' and (value.tools or value.subagents):
                    raise HTTPException(400, CLAUDE_CODE)
                for name in [*(value.tools or []), *value.subagent_blocked]:
                    if name not in CATALOG:
                        raise HTTPException(400, UNKNOWN.format(tool=name))
                    if name == SUBAGENT:
                        raise HTTPException(400, SUBAGENT_SWITCH)
```

- after the loop that inserts the tool rows (inside `if value.runtime == 'openai':`):

```python
                    if value.subagent_blocked:
                        conn.execute('UPDATE ka.agent_tools SET subagent_allowed=false WHERE agent_id=%s AND tool=ANY(%s)',
                                     (agent_id, value.subagent_blocked))
                    if value.subagents:
                        switch_subagents(conn, agent_id, True)
```

- [ ] **Step 7: Run the tests**

Run: `cd services/admin-api && uv run --frozen pytest -q tests/test_subagent_switch.py tests/test_agent_tools_api.py tests/test_tool_catalog.py tests/test_agent_tools_config.py tests/test_agent_clones.py tests/test_agent_apply.py`
Expected: PASS.

Run: `cd services/admin-api && uv run --frozen pytest -q`
Expected: PASS except the known failures.

- [ ] **Step 8: Commit**

```bash
git add services/admin-api/src/knowledge_admin/tools/catalog.py \
  services/admin-api/src/knowledge_admin/knowledge_agents/subagents.py \
  services/admin-api/src/knowledge_admin/knowledge_agents/config.py services/admin-api/src/knowledge_admin/tools_api.py \
  services/admin-api/src/knowledge_admin/profiles_api.py services/admin-api/src/knowledge_admin/agent_wizard.py \
  services/admin-api/tests/test_subagent_switch.py services/admin-api/tests/test_agent_tools_api.py \
  services/admin-api/tests/test_tool_catalog.py
git commit -m "feat(admin-api): May start subagents is the run_subagent row; the Tools tab stores subagent_allowed

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---
### Task 3: The checks and the subagent's configuration (pure), and MCP tools as the model sees them

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/knowledge_agents/subagents.py`
- Create: `services/admin-api/src/knowledge_admin/knowledge_agents/prompts/subagent.md`
- Modify: `services/admin-api/src/knowledge_admin/knowledge_agents/config.py` (add `mcp_definitions`, `credential_references`)
- Modify: `services/admin-api/src/knowledge_admin/agents.py` (`load_credentials`, `mcp_tool_names`, `agent`)
- Modify: `services/admin-api/src/knowledge_admin/capabilities/context.py` (`RunContext.mcp_tools`)
- Create: `services/admin-api/tests/test_subagent_config.py`

**Interfaces:**
- Consumes: `catalog.SUBAGENT`, `catalog.item(..., subagent_allowed=)`, `catalog.tools_of`, tool items with
  `subagent_allowed` (Task 2); `run_resources.KINDS` (`key`, `capability`, `label`).
- Produces (subagents): `MAX_TASK = 20_000`, `MAX_PER_TURN = 16`, `PROMPT: str`, the message constants of Global
  Constraints, `check_task(task: str) -> None`, `subagent_config(config: Mapping, tools: list[str], resources:
  Mapping[str, list[str]] | None, *, offered: Collection[str], mcp_tools: Mapping[str, str]) -> dict[str, Any]`,
  `referenced_credentials(config: Mapping, credentials: Mapping[str, str]) -> dict[str, str]`.
- Produces (config): `mcp_definitions(config) -> list[dict]`, `credential_references(config) -> list[dict | None]`.
- Produces (agents): `async mcp_tool_names(assistant: Agent, context: dict) -> dict[str, str]`; every run context has
  `mcp_tools` (`{}` without MCP servers).

- [ ] **Step 1: Write the failing tests**

Create `services/admin-api/tests/test_subagent_config.py`:

```python
"""run_subagent's checks and the configuration a subagent runs with: only the given tools (MCP tools by the name the
model sees), only resources from the parent's current lists, its own prompt, no instructions or skills, and only the
credentials it references. Pure: no database, no model."""
import asyncio
import copy

import pytest
from agents import RunHooks

from knowledge_admin.agents import agent
from knowledge_admin.knowledge_agents.subagents import (EMPTY_TASK, LONG_TASK, NO_SPAWN, NO_TOOLS, NOT_FOR_SUBAGENTS,
                                                        NOT_GIVEN, NOT_OFFERED, PROMPT, check_task,
                                                        referenced_credentials, subagent_config)
from knowledge_admin.tools.catalog import CATALOG, effective_tools, item, mcp_blocked
from test_sdk_agent import config, responses_api  # noqa: F401  (fixtures)

MODEL = {'id': 'm', 'name': 'Model', 'provider': 'openai', 'api_format': 'responses', 'model': 'gpt-5',
         'base_url': 'http://model.test/v1', 'enabled': True, 'api_key_ref': {'id': 'model-key'}}
ON = {'list_files', 'read_file', 'shell_run', 'code_search', 'ssh_run', 'search_documents', 'run_subagent'}
OFFERED = frozenset(ON)  # what the parent's run offers (context['tools'])
MCP = {'mcp_docs__lookup': 'mcp:docs:lookup', 'mcp_docs__search': 'mcp:docs:search'}  # context['mcp_tools']
CREDENTIALS = {'model-key': 'model-secret', 'ssh-key': 'ssh-secret', 'mcp-token': 'mcp-secret'}
NO_RESOURCES = {'code_project_ids': [], 'collection_ids': [], 'server_ids': [], 'service_ids': []}


def parent(**change) -> dict:
    """A fixed lead agent's pinned configuration: two code projects with graphs, all collections, an SSH server, an MCP
    server with two tools, a service, a skill and instructions; shell_run may not be given to subagents."""
    tools = [item(name, 'on' if name in ON else 'off', 'Read narrowly.' if name == 'read_file' else None,
                  subagent_allowed=name not in ('shell_run', 'run_subagent')) for name in CATALOG]
    value = {
        'id': 'lead', 'name': 'Lead', 'description': '', 'when_to_use': '', 'enabled': True, 'revision': 3,
        'runtime': 'openai', 'resources': 'fixed', 'model': MODEL, 'system_prompt': {'content': 'Lead the work.'},
        'instructions': 'Use the runbook.',
        'code': {'enabled': True, 'all_projects': False, 'graphs': [{'id': 'app', 'name': 'App'}, {'id': 'worker', 'name': 'Worker'}],
                 'projects': [{'id': 'app', 'name': 'App', 'folder_paths': ['/code/app']},
                              {'id': 'worker', 'name': 'Worker', 'folder_paths': ['/code/worker']}]},
        'documents': {'enabled': True, 'grounded': False, 'all_collections': True,
                      'collections': [{'id': 'runbooks', 'name': 'runbooks', 'paths': ['/data/runbooks']}]},
        'ssh': {'enabled': True, 'servers': [{'id': 'prod', 'name': 'prod-1', 'enabled': True, 'ssh_allowed': True,
                                              'private_key_ref': {'id': 'ssh-key'}, 'skills': [], 'mcp_servers': []}]},
        'skills': {'enabled': True, 'items': [{'id': 'inspect', 'name': 'Inspect', 'enabled': True}]},
        'mcp': {'enabled': True, 'servers': [{'id': 'docs', 'name': 'Docs', 'enabled': True,
                                              'bearer_token_ref': {'id': 'mcp-token'},
                                              'tools': [{'name': 'lookup'}, {'name': 'search'}]}]},
        'services': {'enabled': True, 'items': [{'id': 'payments', 'name': 'Payments'}]},
        'tools': {'items': [*tools, item('mcp:docs:lookup', 'on', 'Use for API pages.', group='mcp')]},
        'record': {'run_key': 'run:x'},
    }
    value.update(change)
    return value


def modes(sub) -> dict[str, str]:
    return {entry['tool']: entry['mode'] for entry in sub['tools']['items']}


def test_a_subagent_gets_only_the_given_tools_and_resources_and_its_own_prompt():
    lead = parent()
    before = copy.deepcopy(lead)
    sub = subagent_config(lead, ['read_file', 'mcp_docs__lookup'], {**NO_RESOURCES, 'code_project_ids': ['worker']},
                          offered=OFFERED, mcp_tools=MCP)
    assert lead == before  # the parent's configuration is untouched
    assert {name for name, mode in modes(sub).items() if mode == 'on'} == {'read_file', 'mcp:docs:lookup'}
    assert modes(sub)['run_subagent'] == 'off' and modes(sub)['mcp:docs:search'] == 'off'
    entries = {entry['tool']: entry for entry in sub['tools']['items']}
    assert (entries['read_file']['usage'], entries['mcp:docs:lookup']['usage']) == ('Read narrowly.', 'Use for API pages.')
    assert mcp_blocked(sub, 'docs') == ['search'] and [s['id'] for s in sub['mcp']['servers']] == ['docs']
    assert {spec.name for spec, _ in effective_tools(sub, [])} == {'read_file'}
    assert [p['id'] for p in sub['code']['projects']] == ['worker'] and [g['id'] for g in sub['code']['graphs']] == ['worker']
    assert (sub['documents']['all_collections'], sub['documents']['collections']) == (False, [])
    assert sub['ssh']['servers'] == [] and sub['services']['items'] == []
    assert sub['resources'] == 'per_run' and sub['run_resources'] == {**NO_RESOURCES, 'code_project_ids': ['worker']}
    assert sub['system_prompt'] == {'content': PROMPT} and PROMPT.startswith('You are a helper started by another agent')
    assert (sub['instructions'], sub['skills'], sub['name']) == ('', {'enabled': False, 'items': []}, 'Lead · subagent')
    assert sub['model'] == MODEL and 'record' not in sub


def test_without_resources_a_subagent_keeps_the_parents_lists_and_mode():
    sub = subagent_config(parent(), ['ssh_run'], None, offered=OFFERED, mcp_tools=MCP)
    assert [p['id'] for p in sub['code']['projects']] == ['app', 'worker']
    assert sub['documents']['all_collections'] is True and [s['id'] for s in sub['ssh']['servers']] == ['prod']
    assert sub['resources'] == 'fixed' and 'run_resources' not in sub
    assert (sub['mcp']['enabled'], sub['mcp']['servers']) == (False, [])  # no MCP tool given: no MCP server


@pytest.mark.parametrize('tools, resources, lead, message', [
    ([], None, {}, NO_TOOLS),
    (['run_subagent'], None, {}, NO_SPAWN),
    (['ast_grep'], None, {}, NOT_OFFERED.format(name='ast_grep')),
    (['mcp:docs:lookup'], None, {}, NOT_OFFERED.format(name='mcp:docs:lookup')),  # the Tools tab name is not the model's
    (['shell_run'], None, {}, NOT_FOR_SUBAGENTS.format(name='shell_run')),
    (['read_file'], {'code_project_ids': ['billing']}, {}, NOT_GIVEN.format(label='Code project', id='billing')),
    (['read_file'], {'service_ids': ['ledger']}, {}, NOT_GIVEN.format(label='Service', id='ledger')),
    (['read_file'], {'collection_ids': ['runbooks']}, {'documents': {'enabled': False, 'collections': []}},
     NOT_GIVEN.format(label='Collection', id='runbooks')),  # a kind the parent does not use
    (['read_file'], {'code_project_ids': ['app']},  # a per-run parent's lists are its run's resources
     {'resources': 'per_run', 'run_resources': {**NO_RESOURCES, 'code_project_ids': ['worker']},
      'code': {'enabled': True, 'all_projects': False, 'graphs': [],
               'projects': [{'id': 'worker', 'name': 'Worker', 'folder_paths': ['/code/worker']}]}},
     NOT_GIVEN.format(label='Code project', id='app')),
])
def test_checks_refuse_with_a_message_naming_the_tool_or_resource(tools, resources, lead, message):
    with pytest.raises(ValueError) as refused:
        subagent_config(parent(**lead), tools, resources, offered=OFFERED, mcp_tools=MCP)
    assert str(refused.value) == message


def test_a_task_is_required_and_at_most_20000_characters():
    check_task('x' * 20_000)
    for task, message in (('  ', EMPTY_TASK), ('x' * 20_001, LONG_TASK)):
        with pytest.raises(ValueError) as refused:
            check_task(task)
        assert str(refused.value) == message


def test_a_subagent_gets_only_the_credentials_its_configuration_references():
    lead = parent()
    narrow = subagent_config(lead, ['read_file'], {**NO_RESOURCES, 'code_project_ids': ['worker']}, offered=OFFERED,
                             mcp_tools=MCP)
    assert referenced_credentials(narrow, CREDENTIALS) == {'model-key': 'model-secret'}
    wide = subagent_config(lead, ['ssh_run', 'mcp_docs__lookup'], None, offered=OFFERED, mcp_tools=MCP)
    assert referenced_credentials(wide, CREDENTIALS) == CREDENTIALS


def test_the_run_context_names_each_mcp_tool_as_the_model_sees_it(config, responses_api):
    url, requests = responses_api
    config['mcp'] = {'enabled': True, 'servers': [
        {'id': 'docs-one', 'name': 'Docs', 'description': 'Fixture', 'url': url.removesuffix('/v1') + '/mcp',
         'enabled': True, 'bearer_token_ref': None}]}
    seen: dict[str, str] = {}

    class Capture(RunHooks):
        async def on_agent_start(self, context, agent):
            seen.update(context.context['mcp_tools'])

    asyncio.run(agent(config, 'Describe the tools.', credentials={'model-key': 'fixture-api-secret'}, hooks=Capture()))
    request = next(r for r in requests if r['path'] == '/v1/responses')
    [visible] = [tool['name'] for tool in request['body']['tools'] if 'lookup' in tool['name']]
    assert seen == {visible: 'mcp:docs-one:lookup'}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd services/admin-api && uv run --frozen pytest -q tests/test_subagent_config.py`
Expected: FAIL — `ImportError: cannot import name 'EMPTY_TASK'`.

- [ ] **Step 3: The prompt and the credential references**

Create `services/admin-api/src/knowledge_admin/knowledge_agents/prompts/subagent.md` (one paragraph, newline at end):

```markdown
You are a helper started by another agent for one task. Work only on that task with the tools and resources you were given. Read before you conclude; cite evidence as file:line, document path or command. If the task cannot be done with what you have, say what is missing. Finish with a concise answer for the agent that started you.
```

In `services/admin-api/src/knowledge_admin/knowledge_agents/config.py`, after `require_run_resources`:

```python
def mcp_definitions(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The MCP servers a configuration may connect to: the agent's and, with MCP on, those of its usable SSH servers."""
    if not config['mcp']['enabled']:
        return []
    servers = list(config['mcp']['servers'])
    if config['ssh']['enabled']:
        for server in config['ssh']['servers']:
            if server['enabled'] and server['ssh_allowed']:
                servers.extend(server['mcp_servers'])
    return servers


def credential_references(config: Mapping[str, Any]) -> list[dict[str, Any] | None]:
    """The credential references of the enabled parts of a configuration (None: a part without one): the model key,
    the SSH keys of usable servers and the tokens of enabled MCP servers. Nothing is decrypted here."""
    references = [config['model']['api_key_ref']] if config['model'] is not None else []
    if config['ssh']['enabled']:
        references += [server['private_key_ref'] for server in config['ssh']['servers']
                       if server['enabled'] and server['ssh_allowed']]
    references += [server['bearer_token_ref'] for server in mcp_definitions(config) if server['enabled']]
    return references
```

In `services/admin-api/src/knowledge_admin/agents.py`, extend the config import to
`from knowledge_admin.knowledge_agents.config import credential_references, get_agent_config, mcp_definitions, per_run, require_run_resources  # noqa: F401  (moved; imported from here too)`
and replace the first lines of `load_credentials` (from `references = ...` through `references.extend(...)`) with:

```python
    references = credential_references(config)
    mcp = mcp_definitions(config)
```

(The rest of `load_credentials` is unchanged; it checks `mcp` servers and decrypts `references` as before.)

- [ ] **Step 4: MCP tools as the model sees them**

In `services/admin-api/src/knowledge_admin/capabilities/context.py`: `from typing import Any, NotRequired, TypedDict`
and add to `RunContext`:

```python
    mcp_tools: NotRequired[dict[str, str]]  # the MCP tools as the model sees them → mcp:<server id>:<tool name>
```

In `services/admin-api/src/knowledge_admin/agents.py`, add `RunContextWrapper` to the `from agents import ...` line, add
`'mcp_tools': {}` to the `context` dict literal, add after `context['shell_enabled'] = ...`:

```python
                    context['mcp_tools'] = await mcp_tool_names(assistant, context)
```

and after `create_agent`:

```python
async def mcp_tool_names(assistant: Agent, context: dict[str, Any]) -> dict[str, str]:
    """The run's MCP tools as the model sees them (the SDK's server-prefixed names), each with its Tools tab name
    mcp:<server id>:<tool>. Both listings are the SDK's own (cached, filtered, in the same server and tool order); if
    they ever disagree, no MCP tool is named, so run_subagent cannot give a wrong one."""
    if not assistant.mcp_servers:
        return {}
    wrapper = RunContextWrapper(context)
    visible = await assistant.get_mcp_tools(wrapper)
    names = [f'mcp:{server.name}:{tool.name}' for server in assistant.mcp_servers
             for tool in await server.list_tools(wrapper, assistant)]
    if len(visible) != len(names):
        return {}
    return {tool.name: name for tool, name in zip(visible, names)}
```

- [ ] **Step 5: The checks and the sub-config**

Replace `services/admin-api/src/knowledge_admin/knowledge_agents/subagents.py` with:

```python
"""run_subagent: a native agent hands a separate part of its work to a helper (a subagent) that runs in parallel with
the tools and resources it is given and without the conversation. Subagents run only in native agent sessions (Test
and Generate documents), whose runner puts the turn's Subagents into the run context.

The checks and the subagent's configuration are pure: a deep copy of the parent's effective configuration with only
the given tools on and the resource lists narrowed, the built-in prompt, no instructions and no skills."""
import copy
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any

from knowledge_admin.knowledge_agents.config import credential_references
from knowledge_admin.knowledge_agents.run_resources import KINDS
from knowledge_admin.tools.catalog import CATALOG, SUBAGENT, item, tools_of

MAX_TASK = 20_000
MAX_PER_TURN = 16
PROMPT = (Path(__file__).parent / 'prompts' / 'subagent.md').read_text(encoding='utf-8').strip()
UNAVAILABLE = 'Subagents run only in agent sessions.'
EMPTY_TASK = 'Give the subagent a task.'
LONG_TASK = 'A subagent task is at most 20,000 characters.'
NO_TOOLS = 'Give the subagent at least one tool.'
NO_SPAWN = 'Tool run_subagent cannot be given to a subagent.'
NOT_OFFERED = 'Tool {name} is not available in this run.'
NOT_FOR_SUBAGENTS = 'Tool {name} may not be given to a subagent.'
NOT_GIVEN = '{label} {id} is not one of your resources.'
LIMIT = 'This turn already started 16 subagents.'
FAILED = 'Subagent failed: {message}'


def check_task(task: str) -> None:
    if not task.strip():
        raise ValueError(EMPTY_TASK)
    if len(task) > MAX_TASK:
        raise ValueError(LONG_TASK)


def _listed(config: Mapping[str, Any], capability: str) -> dict[str, dict[str, Any]]:
    """The parent's current resources of one kind, by id; none for a kind it does not use."""
    if not config[capability]['enabled']:
        return {}
    items = {'code': lambda: config['code']['projects'], 'documents': lambda: config['documents'].get('collections', []),
             'ssh': lambda: config['ssh']['servers'], 'services': lambda: config['services']['items']}[capability]()
    return {str(entry['id']): entry for entry in items if 'id' in entry}


def _given_tools(config: Mapping[str, Any], tools: list[str], offered: Collection[str],
                 mcp_tools: Mapping[str, str]) -> tuple[set[str], dict[str, set[str]]]:
    """The given catalog tools, and the given MCP tools by server id. Each name must be offered to this run (MCP
    tools by the name the model sees), allowed for subagents, and not run_subagent."""
    if not tools:
        raise ValueError(NO_TOOLS)
    rows = {entry['tool']: entry for entry in tools_of(config)['items']}
    catalog: set[str] = set()
    mcp: dict[str, set[str]] = {}
    for name in dict.fromkeys(tools):
        if name == SUBAGENT:
            raise ValueError(NO_SPAWN)
        if name in offered:
            row_name = name
        elif name in mcp_tools:
            row_name = mcp_tools[name]
        else:
            raise ValueError(NOT_OFFERED.format(name=name))
        if not rows.get(row_name, {}).get('subagent_allowed', True):
            raise ValueError(NOT_FOR_SUBAGENTS.format(name=name))
        if row_name.startswith('mcp:'):
            _, server, tool = row_name.split(':', 2)
            mcp.setdefault(server, set()).add(tool)
        else:
            catalog.add(row_name)
    return catalog, mcp


def _narrow(sub: dict[str, Any], resources: Mapping[str, list[str]]) -> None:
    """Narrow each resource list to `resources`, as run_resources.resolve narrows a per-run agent's: every id must be
    in the parent's current list; an empty list gives nothing of that kind, never everything."""
    given: dict[str, list[str]] = {}
    chosen: dict[str, list[dict[str, Any]]] = {}
    for kind in KINDS:
        listed = _listed(sub, kind.capability)
        ids = list(dict.fromkeys(str(entry) for entry in resources.get(kind.key) or []))
        for entry in ids:
            if entry not in listed:
                raise ValueError(NOT_GIVEN.format(label=kind.label, id=entry))
        given[kind.key], chosen[kind.key] = ids, [listed[entry] for entry in ids]
    code_ids = set(given['code_project_ids'])
    sub['code'] = {**sub['code'], 'all_projects': False, 'projects': chosen['code_project_ids'],
                   'graphs': [graph for graph in sub['code'].get('graphs', []) if str(graph.get('id')) in code_ids]}
    documents = {key: value for key, value in sub['documents'].items() if key not in ('folders', 'all_folders')}
    sub['documents'] = {**documents, 'all_collections': False, 'collections': chosen['collection_ids']}
    sub['ssh'] = {**sub['ssh'], 'servers': chosen['server_ids']}
    sub['services'] = {**sub['services'], 'items': chosen['service_ids']}
    sub['resources'], sub['run_resources'] = 'per_run', given


def _only(sub: dict[str, Any], catalog: set[str], mcp: dict[str, set[str]], mcp_tools: Mapping[str, str]) -> None:
    """Only the given tools on: catalog tools by name; MCP servers kept only for given MCP tools, every other tool of
    those servers off (from the snapshot's tool lists and the run's live names)."""
    rows = {entry['tool']: entry for entry in tools_of(sub)['items']}
    items = [item(name, 'on' if name in catalog else 'off', rows.get(name, {}).get('usage')) for name in CATALOG]

    def keep(servers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [server for server in servers if str(server['id']) in mcp]
    sub['mcp'] = {**sub['mcp'], 'enabled': bool(mcp) and sub['mcp']['enabled'], 'servers': keep(sub['mcp']['servers'])}
    for server in sub['ssh']['servers']:
        server['mcp_servers'] = keep(server.get('mcp_servers', []))
    kept = [*sub['mcp']['servers'], *(found for server in sub['ssh']['servers'] for found in server['mcp_servers'])]
    for server_id, given in sorted(mcp.items()):
        listed = {tool['name'] for server in kept if str(server['id']) == server_id for tool in server.get('tools', [])}
        listed |= {name.split(':', 2)[2] for name in mcp_tools.values() if name.startswith(f'mcp:{server_id}:')}
        for tool in sorted(listed):
            name = f'mcp:{server_id}:{tool}'
            items.append(item(name, 'on' if tool in given else 'off', rows.get(name, {}).get('usage'), group='mcp'))
    sub['tools'] = {'items': items}


def subagent_config(config: Mapping[str, Any], tools: list[str], resources: Mapping[str, list[str]] | None, *,
                    offered: Collection[str], mcp_tools: Mapping[str, str]) -> dict[str, Any]:
    """The configuration a subagent runs with, or a ValueError naming the tool or resource that cannot be given.
    `offered`: the catalog tools of the parent's run (context['tools']); `mcp_tools`: its MCP tools as the model sees
    them (context['mcp_tools']). resources None: the parent's lists and mode; otherwise a per-run run of exactly those."""
    catalog, mcp = _given_tools(config, tools, offered, mcp_tools)
    sub = copy.deepcopy(dict(config))
    sub.pop('record', None)
    if resources is not None:
        _narrow(sub, resources)
    _only(sub, catalog, mcp, mcp_tools)
    sub['name'] = f"{config['name']} · subagent"
    sub['system_prompt'] = {'content': PROMPT}
    sub['instructions'] = ''
    sub['skills'] = {'enabled': False, 'items': []}
    return sub


def referenced_credentials(config: Mapping[str, Any], credentials: Mapping[str, str]) -> dict[str, str]:
    """The parent turn's already decrypted credentials that a subagent's configuration references; nothing else."""
    wanted = {str(reference['id']) for reference in credential_references(config) if reference is not None}
    return {key: value for key, value in credentials.items() if str(key) in wanted}


async def run_subagent(context: Any, task: str, tools: list[str], resources: dict[str, list[str]] | None) -> str:
    """The catalog tool: the subagent's final answer, or a ValueError the adapter returns as a tool error."""
    host = context.get('subagents')
    if host is None:
        raise ValueError(UNAVAILABLE)
    return await host.run(context, task, tools, resources)
```

- [ ] **Step 6: Run the tests**

Run: `cd services/admin-api && uv run --frozen pytest -q tests/test_subagent_config.py tests/test_sdk_agent.py tests/test_tool_enforcement.py tests/test_native_sessions.py`
Expected: PASS.

Run: `cd services/admin-api && uv run --frozen pytest -q`
Expected: PASS except the known failures.

- [ ] **Step 7: Commit**

```bash
git add services/admin-api/src/knowledge_admin/knowledge_agents/subagents.py \
  services/admin-api/src/knowledge_admin/knowledge_agents/prompts/subagent.md \
  services/admin-api/src/knowledge_admin/knowledge_agents/config.py services/admin-api/src/knowledge_admin/agents.py \
  services/admin-api/src/knowledge_admin/capabilities/context.py services/admin-api/tests/test_subagent_config.py
git commit -m "feat(admin-api): run_subagent checks and the subagent's narrowed configuration

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Running subagents in a session turn

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/capabilities/context.py` (`TOOL_CALL_ID`, `RunContext.subagents`)
- Modify: `services/admin-api/src/knowledge_admin/tools/sdk.py` (`function_tool.invoke`)
- Modify: `services/admin-api/src/knowledge_admin/agent_activity.py:39-50` (`tool_failed`)
- Modify: `services/admin-api/src/knowledge_admin/agents.py` (`agent`: `workspace`, `subagents`)
- Modify: `services/admin-api/src/knowledge_admin/knowledge_agents/native_sessions.py` (`SessionEvents.add`,
  `NativeRunner.__init__`, `run_turn`, `drive`)
- Modify: `services/admin-api/src/knowledge_admin/knowledge_agents/subagents.py` (`SubagentEvents`, `Subagents`,
  `run_subagent` with the call id)
- Modify: `deploy/compose.yaml` (agent-session-worker environment), `tests/test_setup.py:380-383`
- Modify: `services/admin-api/tests/native_fakes.py`
- Create: `services/admin-api/tests/test_subagents.py`

**Interfaces:**
- Consumes: `check_task`, `subagent_config`, `referenced_credentials`, `MAX_PER_TURN`, `LIMIT`, `FAILED`, `UNAVAILABLE`
  (Task 3); `native_sessions.SessionEvents`, `native_sessions.failure`, `session_worker.clean_text`.
- Produces: `capabilities.context.TOOL_CALL_ID: ContextVar[str | None]` (set by `tools/sdk.function_tool` around each
  invocation); `agents.agent(..., workspace: Path | None = None, subagents: Any = None)` (context keys `workspace`
  reused, `subagents`); `SessionEvents.add(kind, payload)`; `subagents.SubagentEvents(parent: SessionEvents,
  parent_call_id: str, *, secrets: list[str], record_usage)`; `subagents.Subagents(parent: SessionEvents, *, secrets,
  record_usage, run_agent, limit: int | None = None)` with `.run(context, task, tools, resources, call_id) -> str`,
  `.model_filter(data) -> ModelInputData`, `.started: int`, `.limit: int`;
  `NativeRunner(store, session, run_agent=agent, *, run_subagent=None, ...)`;
  `agent_activity.tool_failed('Subagent failed: …') is True`.
- Produces (tests): `native_fakes.FakeSubagent(turns: dict[str, dict])`, `native_fakes.delegate(call_id, task, tools,
  resources=None, results=None)`, `native_fakes.together(*steps)`, `Turn.context`, `FakeAgent(..., subagents=None)`
  with a script's `offered` tool names.

- [ ] **Step 1: Let the fakes run subagents**

In `services/admin-api/tests/native_fakes.py`:
- imports: `from pathlib import Path`, `from knowledge_admin.capabilities.context import TOOL_CALL_ID`,
  `from knowledge_admin.knowledge_agents.subagents import run_subagent`;
- `Turn.__init__` gains `context: dict | None = None` (stored as `self.context`; docstring: "and the run context the
  tools see");
- `FakeAgent.__call__` gains a last keyword `subagents=None`, and the `Turn` it builds gets a run context (replace the
  `turn = Turn(...)` line):

```python
            # The run context the tools see, as agent() builds it: `offered` names the run's catalog tools.
            context = {'config': config, 'credentials': credentials, 'workspace': Path('/work/agent-workspaces/turn'),
                       'shell_enabled': False, 'tools': frozenset(script.get('offered', ())), 'mcp_tools': {},
                       'subagents': subagents}
            turn = Turn(record, hooks, writer, call_model_input_filter, [*record['history'], user], context)
```

- append:

```python
class FakeSubagent:
    """Stands in for knowledge_admin.agents.agent when Subagents runs a subagent: the script named by the task plays
    with the subagent's hooks and model filter like a FakeAgent turn, without a session, then raises the script's error
    or returns its answer."""

    def __init__(self, turns: dict[str, dict]):
        self.turns = turns
        self.calls: list[dict] = []

    async def __call__(self, config, task, *, credentials, workspace, hooks, max_turns, turn_timeout_seconds,
                       call_model_input_filter, stream):
        record = {'config': config, 'task': task, 'credentials': dict(credentials), 'workspace': workspace,
                  'max_turns': max_turns, 'turn_timeout_seconds': turn_timeout_seconds, 'stream': stream,
                  'filter': call_model_input_filter, 'inputs': [], 'tools': []}
        self.calls.append(record)
        script = self.turns[task]
        writer = SimpleNamespace(name=config['name'], instructions=config['system_prompt']['content'])
        turn = Turn(record, hooks, writer, call_model_input_filter, [{'role': 'user', 'content': task}])
        await hooks.on_agent_start(None, writer)
        for step in script.get('steps', []):
            await step(turn)
        if 'error' in script:
            raise script['error']
        return SimpleNamespace(final_output=script['answer'])


def delegate(call_id: str, task: str, tools: list[str], resources: dict | None = None, results: list | None = None):
    """The parent's run_subagent call as the SDK runs it: on_tool_start, the catalog tool with this call id (a refusal
    becomes the SDK's error text), on_tool_end with its result. `results` collects the results."""
    async def step(turn: Turn):
        context, function = SimpleNamespace(tool_call_id=call_id), SimpleNamespace(name='run_subagent')
        await turn.hooks.on_tool_start(context, turn.writer, function)
        token = TOOL_CALL_ID.set(call_id)
        try:
            output = await run_subagent(turn.context, task, tools, resources)
        except Exception as exc:
            output = f'An error occurred while running the tool. Please try again. Error: {exc}'
        finally:
            TOOL_CALL_ID.reset(token)
        if results is not None:
            results.append(output)
        await turn.hooks.on_tool_end(context, turn.writer, function, output)
        turn.items.append({'type': 'function_call_output', 'call_id': call_id, 'output': output})
    return step


def together(*steps):
    """Steps the SDK runs at once, such as the function calls of one model response."""
    async def step(turn: Turn):
        await asyncio.gather(*(each(turn) for each in steps))
    return step
```

- [ ] **Step 2: Write the failing tests**

Create `services/admin-api/tests/test_subagents.py`:

```python
"""run_subagent at run time: the Subagents of one native turn (parallel limit, 16 per turn, closed turns, failures,
credentials, events in the parent's turn record with parent_call_id), the adapter's call id, and the worker running
subagents in a session (one record, session totals, Stop)."""
import asyncio
import dataclasses
import json
from pathlib import Path

import pytest
from agents import RunHooks
from agents.tool_context import ToolContext

from native_fakes import FakeAgent, FakeSubagent, Recorder, call, delegate, hang, llm, message, together, tool
from knowledge_admin.agent_activity import tool_failed
from knowledge_admin.agents import agent
from knowledge_admin.capabilities.context import TOOL_CALL_ID
from knowledge_admin.knowledge_agents import native_sessions
from knowledge_admin.knowledge_agents.native_sessions import SessionEvents
from knowledge_admin.knowledge_agents.subagents import FAILED, LIMIT, NOT_OFFERED, PROMPT, Subagents, run_subagent
from knowledge_admin.tools.catalog import CATALOG
from knowledge_admin.tools.sdk import function_tool
from test_claude_code_runner import events as run_events, post, session_row, wait_for
from test_claude_code_sessions import sessions  # noqa: F401  (fixture)
from test_native_sessions import KEY, claimed, leftover_tasks, native  # noqa: F401  (fixture)
from test_repositories import HEADERS
from test_sdk_agent import config, responses_api  # noqa: F401  (fixtures)
from test_subagent_config import CREDENTIALS, MCP, NO_RESOURCES, OFFERED, parent

SECRET = CREDENTIALS['model-key']
SCRATCH = Path('/work/agent-workspaces/lead')


def host(turns: dict, *, limit: int = 4, usage: list | None = None):
    """Subagents of a lead turn whose events record into a fake recorder; usage collects record_usage calls."""
    recorder = Recorder()
    lead = SessionEvents(recorder, secrets=list(CREDENTIALS.values()), first=False)
    fake = FakeSubagent(turns)
    subagents = Subagents(lead, secrets=list(CREDENTIALS.values()), run_agent=fake, limit=limit,
                          record_usage=usage.append if usage is not None else None)
    return subagents, fake, recorder, lead


async def start(subagents, call_id, task, tools=('read_file',), resources=None):
    """The run_subagent tool as the adapter runs it for one call."""
    context = {'config': parent(), 'credentials': dict(CREDENTIALS), 'workspace': SCRATCH, 'shell_enabled': True,
               'tools': OFFERED, 'mcp_tools': MCP, 'subagents': subagents}
    token = TOOL_CALL_ID.set(call_id)
    try:
        return await run_subagent(context, task, list(tools), resources)
    finally:
        TOOL_CALL_ID.reset(token)


def test_a_subagent_runs_its_task_with_its_configuration_and_is_recorded_in_the_turn():
    usage: list[dict] = []
    subagents, fake, recorder, _ = host({'Map the worker': {'steps': [
        llm(call('r1', 'read_file', {'path': '/code/worker/main.py'}), input_tokens=40, output_tokens=5),
        tool('r1', 'read_file', 'def main(): ...'),
        llm(message(f'The worker starts in main.py ({SECRET}).'), input_tokens=60, output_tokens=12)],
        'answer': f'The worker starts in main.py ({SECRET}).'}}, usage=usage)
    answer = asyncio.run(start(subagents, 's1', 'Map the worker',
                               resources={**NO_RESOURCES, 'code_project_ids': ['worker']}))
    assert answer == 'The worker starts in main.py ([redacted]).'
    [run] = fake.calls
    assert run['config']['system_prompt'] == {'content': PROMPT}
    assert [p['id'] for p in run['config']['code']['projects']] == ['worker']
    assert run['credentials'] == {'model-key': SECRET}  # only what its configuration references
    assert (run['workspace'], run['max_turns'], run['turn_timeout_seconds'], run['stream']) == (SCRATCH, None, None, False)
    assert run['filter'] == subagents.model_filter
    assert [kind for kind, _ in recorder.events] == ['input', 'instructions', 'tool_call', 'usage', 'tool_result',
                                                     'message', 'usage']
    assert recorder.events[0] == ('input', {'task': 'Map the worker', 'subagent': {'task': 'Map the worker'},
                                            'parent_call_id': 's1'})
    assert recorder.events[1][1]['text'] == PROMPT
    assert all(payload['parent_call_id'] == 's1' for _, payload in recorder.events)
    assert [each['input_tokens'] for each in usage] == [40, 60]  # every model call reaches the session's totals
    assert subagents.started == 1 and recorder.flushes >= 1


def test_at_most_the_limit_run_at_once_and_later_ones_wait():
    gates = {f'Task {n}': asyncio.Event() for n in range(5)}
    running: list[str] = []

    def script(task):
        async def work(turn):
            running.append(task)
            await gates[task].wait()
        return {'steps': [work], 'answer': task}
    subagents, _, _, _ = host({task: script(task) for task in gates}, limit=4)

    async def scenario():
        calls = [asyncio.create_task(start(subagents, f'c{n}', f'Task {n}')) for n in range(5)]
        while len(running) < 4:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        assert sorted(running) == ['Task 0', 'Task 1', 'Task 2', 'Task 3']  # the fifth waits for a slot
        gates['Task 0'].set()
        while len(running) < 5:
            await asyncio.sleep(0.01)
        for gate in gates.values():
            gate.set()
        return await asyncio.gather(*calls)

    assert asyncio.run(asyncio.wait_for(scenario(), 5)) == [f'Task {n}' for n in range(5)]


def test_a_turn_starts_at_most_16_subagents_and_refused_calls_do_not_count():
    subagents, fake, _, _ = host({f'Task {n}': {'steps': [], 'answer': 'ok'} for n in range(17)}, limit=32)

    async def scenario():
        with pytest.raises(ValueError) as refused:
            await start(subagents, 'bad', 'Task 0', tools=('ast_grep',))
        assert str(refused.value) == NOT_OFFERED.format(name='ast_grep')
        return await asyncio.gather(*(start(subagents, f'c{n}', f'Task {n}') for n in range(17)),
                                    return_exceptions=True)

    results = asyncio.run(scenario())
    assert len(fake.calls) == 16 and subagents.started == 16
    assert [str(result) for result in results if isinstance(result, ValueError)] == [LIMIT]


def test_a_failed_subagent_returns_a_redacted_failure_and_is_recorded():
    subagents, _, recorder, _ = host({'Check prod': {'steps': [], 'error': ValueError(f'SSH key {SECRET} refused')},
                                      'Crash': {'steps': [], 'error': RuntimeError(SECRET)}})

    async def scenario():
        return [await start(subagents, 's1', 'Check prod'), await start(subagents, 's2', 'Crash')]

    assert asyncio.run(scenario()) == [
        FAILED.format(message='SSH key [redacted] refused'),
        FAILED.format(message='Agent execution failed (RuntimeError). Check the model connection and agent resources.')]
    errors = [payload for kind, payload in recorder.events if kind == 'error']
    assert [error['parent_call_id'] for error in errors] == ['s1', 's2'] and SECRET not in json.dumps(errors)
    assert tool_failed(FAILED.format(message='boom')) and not tool_failed('Subagent found the bug.')


def test_a_closed_turn_starts_no_subagent_model_call_tool_call_or_subagent():
    for task, late in (('Model', llm(message('late'))), ('Tool', tool('r1', 'read_file', 'late'))):
        subagents, fake, recorder, lead = host({})

        async def stop(turn, lead=lead):
            lead.closed = True  # Stop, Close or the end of the turn while the subagent works
        fake.turns[task] = {'steps': [stop, late], 'answer': 'late'}
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(start(subagents, 's1', task))
        assert [kind for kind, _ in recorder.events] == ['input', 'instructions'], task
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(start(subagents, 's2', task))  # a call after the turn closed starts nothing
        assert (len(fake.calls), subagents.started) == (1, 1)


def test_the_adapter_publishes_the_call_id_to_the_tool():
    seen = []

    async def remember(context, arguments):
        seen.append(TOOL_CALL_ID.get())
        return 'ok'
    read = function_tool(dataclasses.replace(CATALOG['read_file'], run=remember), CATALOG['read_file'].usage)
    payload = json.dumps({'path': '/a.txt'})
    wrapped = ToolContext(context={'tools': frozenset({'read_file'}), 'credentials': {}}, tool_name='read_file',
                          tool_call_id='call-7', tool_arguments=payload)
    assert asyncio.run(read.on_invoke_tool(wrapped, payload)) == 'ok'
    assert seen == ['call-7'] and TOOL_CALL_ID.get() is None


def test_a_run_can_share_a_scratch_workspace_and_carries_the_turns_subagents(config, responses_api, tmp_path):
    scratch = tmp_path / 'scratch'
    scratch.mkdir()
    seen = {}

    class Capture(RunHooks):
        async def on_agent_start(self, context, agent):
            seen.update(workspace=context.context['workspace'], subagents=context.context['subagents'])
    marker = object()
    asyncio.run(agent(config, 'Describe the tools.', credentials={'model-key': 'fixture-api-secret'}, hooks=Capture(),
                      workspace=scratch, subagents=marker, stream=False))
    assert seen == {'workspace': scratch.resolve(), 'subagents': marker}
    assert scratch.is_dir()  # the parent's workspace outlives the subagent's run


def test_a_session_turn_records_its_subagents_in_one_record_and_counts_their_usage(native):
    sessions, sid = native
    store = sessions.store
    results: list[str] = []
    subagent = FakeSubagent({
        'Read the worker': {'steps': [llm(call('r1', 'read_file', {'path': 'w.py'}), input_tokens=30, output_tokens=3),
                                      tool('r1', 'read_file', f'token {KEY}'),
                                      llm(message('Worker reads w.py.'), input_tokens=20, output_tokens=4)],
                            'answer': 'Worker reads w.py.'},
        'Read the app': {'steps': [llm(message('App reads a.py.'), input_tokens=10, output_tokens=2)],
                         'answer': 'App reads a.py.'}})
    fake = FakeAgent([{'offered': ['read_file', 'run_subagent'], 'steps': [
        llm(call('s1', 'run_subagent', {'task': 'Read the worker', 'tools': ['read_file'], 'resources': None}),
            call('s2', 'run_subagent', {'task': 'Read the app', 'tools': ['read_file'], 'resources': None}),
            input_tokens=100, output_tokens=10),
        together(delegate('s1', 'Read the worker', ['read_file'], results=results),
                 delegate('s2', 'Read the app', ['read_file'], results=results)),
        llm(message('Both read.'), input_tokens=50, output_tokens=5)], 'answer': 'Both read.'}])

    async def scenario():
        runner = asyncio.create_task(native_sessions.NativeRunner(store, claimed(store), fake, run_subagent=subagent,
                                                                  poll=0.02).run())
        await wait_for(store, sid, lambda s: s['status'] == 'waiting' and s['turns'] == 1)
        await post(sessions, f'/api/claude-code/sessions/{sid}/close')
        await asyncio.wait_for(runner, 5)

    asyncio.run(scenario())
    assert sorted(results) == ['App reads a.py.', 'Worker reads w.py.']
    assert subagent.calls[0]['credentials'] == {sessions.claude: KEY}  # the turn's own, not decrypted again
    recorded = [e for e in run_events(store, sid) if e['run_key'] == f'cc:{sid}:1']
    assert [e['seq'] for e in recorded] == list(range(1, len(recorded) + 1))  # one record, every event once, in order
    by_call = {cid: [e['kind'] for e in recorded if e['payload'].get('parent_call_id') == cid] for cid in ('s1', 's2')}
    assert by_call == {'s1': ['input', 'instructions', 'tool_call', 'usage', 'tool_result', 'message', 'usage'],
                       's2': ['input', 'instructions', 'message', 'usage']}
    assert [e['kind'] for e in recorded if 'parent_call_id' not in e['payload']][-1] == 'end'
    assert [e['payload']['summary'] for e in recorded
            if e['kind'] == 'tool_call' and e['payload']['name'] == 'run_subagent'] == ['Read the worker', 'Read the app']
    assert KEY not in json.dumps(recorded, default=str)
    row = session_row(store, sid)
    assert (row['input_tokens'], row['output_tokens'], row['model_calls']) == (210, 24, 5)  # subagents included
    detail = sessions.client.get(f'/api/claude-code/sessions/{sid}', headers=HEADERS).json()
    assert {count['name']: count['calls'] for count in detail['tool_counts']} == {'run_subagent': 2, 'read_file': 1}


def test_stop_cancels_a_running_subagent_and_leaves_nothing_running(native):
    sessions, sid = native
    store, url = sessions.store, f'/api/claude-code/sessions/{sid}'
    started, cancelled = asyncio.Event(), []
    subagent = FakeSubagent({'Watch the queue': {'steps': [hang('tool', started, cancelled)], 'answer': 'never'}})
    fake = FakeAgent([{'offered': ['read_file'], 'steps': [
        llm(call('s1', 'run_subagent', {'task': 'Watch the queue', 'tools': ['read_file'], 'resources': None})),
        delegate('s1', 'Watch the queue', ['read_file'])], 'answer': 'never'}])

    async def scenario():
        runner = asyncio.create_task(native_sessions.NativeRunner(store, claimed(store), fake, run_subagent=subagent,
                                                                  poll=0.02).run())
        await asyncio.wait_for(started.wait(), 5)
        assert (await post(sessions, url + '/stop')).status_code == 202
        await wait_for(store, sid, lambda s: s['status'] == 'stopped')
        await post(sessions, url + '/close')
        await asyncio.wait_for(runner, 5)
        return leftover_tasks()

    assert asyncio.run(scenario()) == []
    assert cancelled == ['tool']
    recorded = run_events(store, sid)
    assert [e['payload'].get('parent_call_id') for e in recorded if e['kind'] == 'input'] == [None, 's1']
    assert [e['payload']['status'] for e in recorded if e['kind'] == 'end'] == ['stopped']
```

- [ ] **Step 3: Run them to verify they fail**

Run: `cd services/admin-api && uv run --frozen pytest -q tests/test_subagents.py`
Expected: FAIL — `ImportError: cannot import name 'TOOL_CALL_ID'` (then `Subagents`).

- [ ] **Step 4: The call id and failed results**

In `services/admin-api/src/knowledge_admin/capabilities/context.py` add `from contextvars import ContextVar`, the
`RunContext` key

```python
    subagents: NotRequired[Any]  # the turn's knowledge_agents.subagents.Subagents (native sessions only)
```

and after `RunContext`:

```python
# The id of the tool call running in this task: tools/sdk.py sets it around each invocation, run_subagent records its
# subagent under it. Concurrent calls run in their own tasks, so each sees its own id.
TOOL_CALL_ID: ContextVar[str | None] = ContextVar('tool_call_id', default=None)
```

In `services/admin-api/src/knowledge_admin/tools/sdk.py` import `TOOL_CALL_ID` from `knowledge_admin.capabilities.context`
and replace `invoke`:

```python
    async def invoke(ctx: ToolContext[Any], raw: str) -> str:
        token = TOOL_CALL_ID.set(ctx.tool_call_id)  # run_subagent records its subagent under this call
        try:
            if spec.name not in ctx.context.get('tools', ()):
                raise ValueError(NOT_OFFERED.format(name=spec.name))
            arguments = spec.arguments.model_validate_json(raw or '{}')
            return await spec.run(ctx.context, arguments)
        except Exception as exc:
            error = redact(str(exc), ctx.context.get('credentials', {}).values())
            span = get_current_span()
            if span is not None:
                span.set_error(SpanError(message='Error running tool (non-fatal)',
                                         data={'tool_name': spec.name, 'error': error}))
            logger.error('Tool %s failed: %s', spec.name, error)
            return f'An error occurred while running the tool. Please try again. Error: {error}'
        finally:
            TOOL_CALL_ID.reset(token)
```

In `services/admin-api/src/knowledge_admin/agent_activity.py`, `tool_failed`: docstring `"""A tool result reported
as failed: the SDK's error text, a failed subagent (run_subagent), or JSON with error, isError, timed_out or a non-zero
exit_code."""` and first line

```python
    failed = detail.startswith(('An error occurred while running the tool', 'Subagent failed: '))
```

- [ ] **Step 5: `agent()` shares a workspace and carries the turn's subagents**

In `services/admin-api/src/knowledge_admin/agents.py`: `from contextlib import AsyncExitStack, asynccontextmanager,
nullcontext`; the signature gains `workspace: Path | None = None, subagents: Any = None` after
`call_model_input_filter`; the docstring gains `workspace: a scratch workspace to share (a subagent uses its
parent's); None: a new one for this run, removed afterwards. subagents: the session turn's Subagents, which
run_subagent finds in the run context.`; and the workspace block becomes:

```python
    task_input = task if session is not None else [*(history or []), {'role': 'user', 'content': task}]
    shared = workspace is not None
    scratch = nullcontext(str(workspace)) if shared else TemporaryDirectory(
        prefix='knowledge-agent-work-', dir=os.environ.get('AGENT_WORKSPACE_ROOT'))
    with scratch as directory:
        workspace = Path(directory).resolve()
        if not shared and os.environ.get('AGENT_SHELL_URL') and os.geteuid() == 0:
            os.chown(workspace, 1000, 1000)  # The unprivileged shell-worker user owns its scratch files.
        context = {'config': config, 'credentials': credentials, 'workspace': workspace,
                   'shell_enabled': False, 'tools': frozenset(), 'mcp_tools': {}, 'subagents': subagents}
```

(the rest of the body is unchanged and stays inside this `with`).

- [ ] **Step 6: Subagent events and the turn's Subagents**

In `services/admin-api/src/knowledge_admin/knowledge_agents/native_sessions.py`:
- `SessionEvents` gains, after `_flush`:

```python
    def add(self, kind: str, payload: dict[str, Any]) -> None:
        """One event of this turn (a subagent's SubagentEvents marks its own with the run_subagent call id)."""
        self.recorder.add(kind, payload)
```

  and every `self.recorder.add(` in `on_agent_start`, `on_llm_end` and `on_tool_end` becomes `self.add(`;
- `summary` gains, before its final `return ''`, a line for the workspace's tool line:

```python
    if name == 'run_subagent':
        return value('task')
```

- `NativeRunner.__init__`:

```python
    def __init__(self, store: RepositoryStore, session: dict, run_agent=agent, *, run_subagent=None, poll: float = 1.0,
                 idle_release: float = IDLE_RELEASE_SECONDS, slot: int = 1):
        super().__init__(store, session, poll=poll, idle_release=idle_release, slot=slot)
        self.run_agent = run_agent
        self.run_subagent = run_subagent or run_agent  # how subagents run (tests pass a stand-in)
```

- in `run_turn`, after `events = SessionEvents(...)`:

```python
        # Imported here: the subagent runtime builds on this module's SessionEvents.
        from knowledge_admin.knowledge_agents.subagents import Subagents
        subagents = Subagents(events, secrets=secrets, record_usage=events.record_usage, run_agent=self.run_subagent)
```

  and the drive call becomes `status, result = await self.drive(config, text, credentials, history, events, comments,
  subagents)`;
- `drive` gains the parameter `subagents` (after `comments`, docstring unchanged) and passes it on:

```python
        turn = asyncio.create_task(self.run_agent(
            config, text, credentials=credentials, session=history, hooks=events, max_turns=None,
            turn_timeout_seconds=None, call_model_input_filter=comments, stream=False, subagents=subagents))
```

In `services/admin-api/src/knowledge_admin/knowledge_agents/subagents.py`:
- module docstring, append: `Running (Subagents): one per native turn, in parallel up to AGENT_MAX_SUBAGENTS, each
  recorded in the turn's record with parent_call_id and counted in the session's totals; Stop cancels them with the
  turn.`;
- imports become:

```python
import asyncio
import copy
import os
from collections.abc import Awaitable, Callable, Collection, Mapping
from pathlib import Path
from typing import Any

from agents.run_config import CallModelData, ModelInputData

from knowledge_admin.capabilities.context import TOOL_CALL_ID
from knowledge_admin.knowledge_agents.config import credential_references
from knowledge_admin.knowledge_agents.native_sessions import SessionEvents, failure
from knowledge_admin.knowledge_agents.run_resources import KINDS
from knowledge_admin.knowledge_agents.session_worker import clean_text
from knowledge_admin.tools.catalog import CATALOG, SUBAGENT, item, tools_of
```

- `run_subagent` passes the call id:

```python
async def run_subagent(context: Any, task: str, tools: list[str], resources: dict[str, list[str]] | None) -> str:
    """The catalog tool: the subagent's final answer, or a ValueError the adapter returns as a tool error."""
    host = context.get('subagents')
    if host is None:
        raise ValueError(UNAVAILABLE)
    return await host.run(context, task, tools, resources, TOOL_CALL_ID.get() or '')
```

- append:

```python
class SubagentEvents(SessionEvents):
    """A subagent's SDK lifecycle as events of its parent's turn record: the kinds SessionEvents writes, each with
    parent_call_id (the run_subagent call). It is closed exactly while the parent's events are (Stop, Close, a lost
    lease, the end of the turn), so a subagent that outlives its turn starts no tool call and records nothing more."""

    def __init__(self, parent: SessionEvents, parent_call_id: str, *, secrets: list[str],
                 record_usage: Callable[[dict[str, Any]], None] | None):
        super().__init__(parent.recorder, secrets=secrets, first=True, record_usage=record_usage)
        self.parent, self.parent_call_id = parent, parent_call_id

    @property
    def closed(self) -> bool:
        return self.parent.closed

    @closed.setter
    def closed(self, value: bool) -> None:
        pass  # follows the parent's events

    def add(self, kind: str, payload: dict[str, Any]) -> None:
        self.recorder.add(kind, {**payload, 'parent_call_id': self.parent_call_id})


class Subagents:
    """The subagents of one native session turn (NativeRunner.run_turn makes one; agent() puts it in the run context).
    At most MAX_PER_TURN are started; `limit` run at once (AGENT_MAX_SUBAGENTS, default 4) and later calls wait for a
    slot. Each runs through `run_agent` (knowledge_admin.agents.agent) with the parent's scratch workspace, no history,
    no turn or time limit, SubagentEvents hooks and `model_filter`, and only the parent turn's credentials its
    configuration references. Cancelling the turn (Stop) cancels the tool call and with it the subagent's run."""

    def __init__(self, parent: SessionEvents, *, secrets: list[str],
                 record_usage: Callable[[dict[str, Any]], None] | None, run_agent: Callable[..., Awaitable[Any]],
                 limit: int | None = None):
        self.parent, self.secrets, self.record_usage, self.run_agent = parent, secrets, record_usage, run_agent
        self.limit = limit or int(os.environ.get('AGENT_MAX_SUBAGENTS', '4'))
        self.slots = asyncio.Semaphore(self.limit)
        self.started = 0

    async def model_filter(self, data: CallModelData) -> ModelInputData:
        """Like the parent's comments filter for a closed turn: no new model request once the turn has ended (one
        already sent cannot be recalled). No comments are delivered to subagents."""
        if self.parent.closed:
            raise asyncio.CancelledError
        return data.model_data

    async def run(self, context: Mapping[str, Any], task: str, tools: list[str],
                  resources: Mapping[str, list[str]] | None, call_id: str) -> str:
        """One run_subagent call: the subagent's final answer (redacted), FAILED with the redacted reason when it fails,
        or a ValueError for a refused call (refused calls do not count towards MAX_PER_TURN)."""
        check_task(task)
        sub = subagent_config(context['config'], tools, resources, offered=context['tools'],
                              mcp_tools=context.get('mcp_tools', {}))
        if self.parent.closed:
            raise asyncio.CancelledError
        if self.started >= MAX_PER_TURN:
            raise ValueError(LIMIT)
        self.started += 1  # no await since the check: calls arriving together cannot pass it twice
        credentials = referenced_credentials(sub, context['credentials'])
        events = SubagentEvents(self.parent, call_id, secrets=self.secrets, record_usage=self.record_usage)
        events.add('input', {'task': task, 'subagent': {'task': task}})
        await asyncio.to_thread(self.parent.recorder.flush)
        async with self.slots:
            try:
                result = await self.run_agent(
                    sub, task, credentials=credentials, workspace=context['workspace'], hooks=events, max_turns=None,
                    turn_timeout_seconds=None, call_model_input_filter=self.model_filter, stream=False)
            except Exception as exc:
                message = failure(exc, self.secrets)
                events.add('error', {'type': type(exc).__name__, 'message': message})
                await asyncio.to_thread(self.parent.recorder.flush)
                return FAILED.format(message=message)
        return clean_text(events.draft(result.final_output), self.secrets)
```

- [ ] **Step 7: The worker's setting**

In `deploy/compose.yaml`, in the `agent-session-worker` environment after `AGENT_SESSION_MAX_SESSIONS: ...` add:

```yaml
      AGENT_MAX_SUBAGENTS: ${AGENT_MAX_SUBAGENTS:-4}
```

In `tests/test_setup.py` replace lines 380–383 with:

```python
            self.assertEqual((env["AGENT_SHELL_URL"], env["AGENT_WORKSPACE_ROOT"], env["CODE_ROOT"], env["DATA_ROOT_DIR"],
                              env["DOCS_WIKI_RAG_URL"], env["DOCS_WIKI_PHOENIX_ENDPOINT"], env["AGENT_SESSION_MAX_SESSIONS"],
                              env["AGENT_MAX_SUBAGENTS"]),
                             ("http://shell-worker:8796", "/work/agent-workspaces", "/work/data/code", "/work/data",
                              "http://rag:8790", "http://phoenix:6006/v1/traces", "4", "4"))
```

- [ ] **Step 8: Run the tests**

Run: `cd services/admin-api && uv run --frozen pytest -q tests/test_subagents.py tests/test_native_sessions.py tests/test_native_session_events.py tests/test_test_session_worker.py tests/test_tool_catalog.py tests/test_sdk_agent.py`
Expected: PASS.

Run: `cd services/admin-api && uv run --frozen pytest -q`
Expected: PASS except the known failures.

Run: `python3 -m unittest discover -s tests`
Expected: OK.

- [ ] **Step 9: Commit**

```bash
git add services/admin-api/src/knowledge_admin/capabilities/context.py services/admin-api/src/knowledge_admin/tools/sdk.py \
  services/admin-api/src/knowledge_admin/agent_activity.py services/admin-api/src/knowledge_admin/agents.py \
  services/admin-api/src/knowledge_admin/knowledge_agents/native_sessions.py \
  services/admin-api/src/knowledge_admin/knowledge_agents/subagents.py deploy/compose.yaml tests/test_setup.py \
  services/admin-api/tests/native_fakes.py services/admin-api/tests/test_subagents.py
git commit -m "feat(admin-api): native session turns run subagents in parallel, recorded in the turn

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: admin-ui — the General switch, the Tools tab's Agents group and the wizard

**Files:**
- Modify: `services/admin-ui/app/lib/profiles.ts` (`AgentProfile.subagents`, `saveAgent`)
- Modify: `services/admin-ui/app/lib/tools.ts` (texts, `saveAgentTools`, `impliedTools`)
- Modify: `services/admin-ui/app/lib/agentWizard.ts` (`NewAgent`)
- Modify: `services/admin-ui/app/components/AgentProfileSettings.tsx` (General: after the **Resources** field)
- Modify: `services/admin-ui/app/components/AgentToolsView.tsx`
- Modify: `services/admin-ui/app/components/AgentWizard.tsx`
- Create: `services/admin-ui/app/lib/__tests__/agentRequests.test.ts`
- Modify tests: `services/admin-ui/app/components/__tests__/agentTools.test.tsx`,
  `services/admin-ui/app/components/__tests__/agentTypes.test.tsx`,
  `services/admin-ui/app/components/__tests__/agentWizard.test.tsx`

**Interfaces:**
- Consumes: `GET/PUT /api/agents/{id}` `subagents`; `PUT /api/agents/{id}/tools` entries with `subagent_allowed`; the
  `run_subagent` row (group `agents`) listed only while the switch is on; wizard `subagents`, `subagent_blocked`
  (Task 2).
- Produces: `tools.SUBAGENT_TOOL = 'run_subagent'`, `tools.SUBAGENTS_HELP`, `tools.SUBAGENTS_OFF_NOTE`;
  `AgentProfile.subagents?: boolean`; `NewAgent.subagents?`, `NewAgent.subagent_blocked?`; `keepChoices` keeps
  `subagent_allowed`.

- [ ] **Step 1: Write the failing tests**

Create `services/admin-ui/app/lib/__tests__/agentRequests.test.ts`:

```ts
import { afterEach, expect, test, vi } from 'vitest';
import { saveAgent, type AgentProfile } from '../profiles';
import { impliedTools, saveAgentTools, type AgentTool, type CatalogTool } from '../tools';

afterEach(() => vi.unstubAllGlobals());
const sent = () => JSON.parse(String(vi.mocked(fetch).mock.calls[0][1]?.body));
const lead = { id: 'lead', name: 'Lead', description: '', when_to_use: '', enabled: true, revision: 3 } as AgentProfile;

test('a tools save sends what a subagent may use', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ runtime: 'openai', revision: 5, tools: [] })));
  await saveAgentTools('lead', 4, [{ tool: 'read_file', mode: 'on', usage: null, subagent_allowed: false } as AgentTool]);
  expect(sent()).toEqual({ expected_revision: 4, tools: [{ tool: 'read_file', mode: 'on', usage: null, subagent_allowed: false }] });
});

test('General sends May start subagents for native agents only', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json(lead)));
  await saveAgent({ ...lead, runtime: 'openai', subagents: true });
  expect(sent().subagents).toBe(true);
  vi.mocked(fetch).mockClear();
  await saveAgent({ ...lead, runtime: 'claude_code', subagents: true });
  expect(sent()).not.toHaveProperty('subagents');
});

test('new agents never get run_subagent from their capabilities', () => {
  const catalog: CatalogTool[] = [
    { name: 'read_file', group: 'code', label: 'Read file', summary: '', usage: '', needs: 'files' },
    { name: 'run_subagent', group: 'agents', label: 'Start subagents', summary: '', usage: '', needs: null }];
  expect(impliedTools(catalog, { repositories_enabled: true, documents_enabled: false, ssh_enabled: false })).toEqual(['read_file']);
});
```

In `services/admin-ui/app/components/__tests__/agentTools.test.tsx`:
- in `the Tools tab switches tools, edits and resets usage and saves once` replace
  `expect(screen.queryByRole('group', { name: 'Agents' })).toBeNull();  // empty groups are hidden` with:

```tsx
  expect(screen.queryByRole('group', { name: 'Documents' })).toBeNull();  // empty groups are hidden
  expect(within(screen.getByRole('group', { name: 'Agents' })).getByText(tools.SUBAGENTS_OFF_NOTE)).toBeVisible();
```

- add after it:

```tsx
test('with subagents on, the Agents group edits run_subagent and what a subagent may use', async () => {
  const user = userEvent.setup();
  vi.mocked(tools.getAgentTools).mockResolvedValue({ ...structuredClone(native), tools: [...structuredClone(native.tools),
    tool({ tool: 'run_subagent', label: 'Start subagents', summary: 'Hand a separate part of the work to a helper that runs in parallel.',
      group: 'agents', subagent_allowed: false, default_usage: 'Start a helper for a separate, well-defined part of your work.', tab: null })] });
  open('/agents/payments?view=tools');
  const agents = await screen.findByRole('group', { name: 'Agents' });
  const start = within(agents).getByRole('switch', { name: 'Start subagents' });
  expect(start).toBeChecked();
  expect(start).toBeDisabled();  // switched with May start subagents on General
  const may = within(agents).getByRole('group', { name: 'A subagent may use' });
  expect(within(may).getByRole('checkbox', { name: 'Read file' })).toBeChecked();
  expect(within(may).getByRole('checkbox', { name: 'Docs / lookup' })).toBeChecked();
  expect(within(may).queryByRole('checkbox', { name: 'Shell' })).toBeNull();  // off: not one of the agent's tools
  expect(within(may).queryByRole('checkbox', { name: 'Start subagents' })).toBeNull();
  await user.click(within(may).getByRole('checkbox', { name: 'Read file' }));
  await user.click(within(agents).getByRole('button', { name: 'Edit usage for Start subagents' }));
  const usage = screen.getByLabelText('Usage for Start subagents');
  await user.clear(usage); await user.type(usage, 'Split the work by project.');
  expect(within(agents).getByText('Edited')).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Save tools' }));
  await waitFor(() => expect(tools.saveAgentTools).toHaveBeenCalledTimes(1));
  const saved = vi.mocked(tools.saveAgentTools).mock.calls[0][2];
  expect(saved.find(item => item.tool === 'read_file')?.subagent_allowed).toBe(false);
  expect(saved.find(item => item.tool === 'run_subagent')).toMatchObject({ mode: 'on', usage: 'Split the work by project.' });
  await user.click(within(agents).getByRole('button', { name: 'Edit usage for Start subagents' }));
  await user.click(within(agents).getByRole('button', { name: 'Reset usage for Start subagents' }));
  expect(within(agents).queryByText('Edited')).toBeNull();
  expect(screen.getByLabelText('Usage for Start subagents')).toHaveValue('Start a helper for a separate, well-defined part of your work.');
});
```

- in `fresh rows keep only the choices the user touched`, give the draft's `read_file` `subagent_allowed: false` and
  expect it kept:

```tsx
  const draft = [tool({ usage: 'Narrow reads.', subagent_allowed: false }), tool({ tool: 'shell_run', mode: 'on' }), tool({ tool: 'ssh_run', group: 'servers', mode: 'off' })];
  // ssh_run was switched on by a General save; its stale 'off' in the draft was never touched.
  expect(keepChoices(fresh, draft, new Set(['read_file', 'shell_run'])).map(item => [item.tool, item.mode, item.usage, item.subagent_allowed])).toEqual([
    ['read_file', 'on', 'Narrow reads.', false], ['shell_run', 'on', null, true], ['ssh_run', 'on', null, true]]);
```

Append to `services/admin-ui/app/components/__tests__/agentTypes.test.tsx`:

```tsx
test('General switches May start subagents, for native agents only', async () => {
  const user = userEvent.setup();
  const view = open('/agents/search');
  const subagents = await screen.findByRole('switch', { name: 'May start subagents' });
  expect(subagents).not.toBeChecked();
  expect(screen.getByText('The agent can hand separate parts of its work to helpers that run in parallel.')).toBeVisible();
  await user.click(subagents);
  await user.click(screen.getByRole('button', { name: 'Save details' }));
  await waitFor(() => expect(profiles.saveAgent).toHaveBeenCalledWith(expect.objectContaining({ id: 'search', subagents: true })));
  view.unmount();
  open('/agents/writer');
  await screen.findByRole('combobox', { name: 'Agent type' });
  expect(screen.queryByRole('switch', { name: 'May start subagents' })).toBeNull();
});
```

In `services/admin-ui/app/components/__tests__/agentWizard.test.tsx`: import `within` from `@testing-library/react`;
add to `catalogTools` (before `] as const`):

```tsx
  { name: 'run_subagent', group: 'agents', label: 'Start subagents', summary: 'Hand work to a helper.', usage: '', needs: null },
```

and append:

```tsx
test('the wizard Tools page switches May start subagents and chooses what a subagent may use', async () => {
  const { user } = await open();
  await user.click(screen.getByRole('button', { name: 'Generate proposal' }));
  await screen.findByLabelText('Agent name');
  await user.click(screen.getByRole('tab', { name: 'Tools' }));
  expect(screen.queryByRole('switch', { name: 'Start subagents' })).toBeNull();  // not a tool to switch: May start subagents is
  const subagents = screen.getByRole('switch', { name: 'May start subagents' });
  expect(subagents).not.toBeChecked();
  expect(screen.queryByRole('group', { name: 'A subagent may use' })).toBeNull();
  await user.click(subagents);
  const may = screen.getByRole('group', { name: 'A subagent may use' });
  for (const name of ['Read file', 'Code search', 'Search documents', 'SSH']) expect(within(may).getByRole('checkbox', { name })).toBeChecked();
  await user.click(within(may).getByRole('checkbox', { name: 'SSH' }));
  await user.click(screen.getByRole('button', { name: 'Create agent' }));
  const created = vi.mocked(api.createWizardAgent).mock.calls[0][0];
  expect(created).toMatchObject({ subagents: true, subagent_blocked: ['ssh_run'] });
  expect(created).not.toHaveProperty('tools');  // the tools still follow the capabilities
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd services/admin-ui && npx vitest run app/lib/__tests__/agentRequests.test.ts app/components/__tests__/agentTools.test.tsx app/components/__tests__/agentTypes.test.tsx app/components/__tests__/agentWizard.test.tsx`
Expected: FAIL — no `subagent_allowed` in the request body, no Agents group, no **May start subagents** switch.

- [ ] **Step 3: The client libraries**

`services/admin-ui/app/lib/tools.ts`:
- after `TOOLS_TAB_NOTE` add:

```ts
export const SUBAGENT_TOOL = 'run_subagent';
export const SUBAGENTS_HELP = 'The agent can hand separate parts of its work to helpers that run in parallel.';
export const SUBAGENTS_OFF_NOTE = 'Turn on May start subagents in General to let this agent start helpers.';
```

- `saveAgentTools` body: `tools: tools.map(({ tool, mode, usage, subagent_allowed }) => ({ tool, mode: mode === 'on' ? 'on' : 'off', usage, subagent_allowed }))`
  and its comment `/** Replaces the agent's tools; a usage of null keeps the built-in text; subagent_allowed: whether run_subagent may give it to a subagent. */`;
- `impliedTools`:

```ts
/** The tools a new agent gets from its capabilities (the backend's enabled_capability_tools); never run_subagent,
 * which comes with May start subagents. */
export const impliedTools = (catalog: CatalogTool[], s: ToolSwitches) =>
  catalog.filter(t => t.group !== 'agents' && toolNeedMet(t.needs, s)).map(t => t.name);
```

`services/admin-ui/app/lib/profiles.ts`:
- `AgentProfile` gains `subagents?: boolean;` (after `resources?: AgentResources;`, comment-free);
- `saveAgent` destructures `subagents` too and sends it for native agents only:

```ts
export const saveAgent = (value: AgentProfile, skillIds?: string[]) => {
  const { revision, name, description, when_to_use, enabled, all_collections, all_repositories, collection_ids, repository_ids, service_ids, documents_enabled, repositories_enabled, ssh_enabled, skills_enabled, mcp_enabled, services_enabled, documents_grounded, runtime, claude_code_tools, resources, subagents } = value;
  return request<AgentProfile>(path(value.id), json('PUT', { ...(skillIds === undefined ? {} : { skill_ids: skillIds }), revision, name, description, when_to_use, enabled, all_collections, all_repositories, collection_ids, repository_ids, service_ids, documents_enabled, repositories_enabled, ssh_enabled, skills_enabled, mcp_enabled, services_enabled, documents_grounded,
    ...(runtime ? { runtime } : {}), ...(resources ? { resources } : {}), ...(claude_code_tools ? { claude_code_tools } : {}),
    ...(runtime !== 'claude_code' && subagents !== undefined ? { subagents } : {}) }));
};
```

`services/admin-ui/app/lib/agentWizard.ts`: `NewAgent` becomes

```ts
export type NewAgent = AgentProposal & { runtime: AgentRuntime; resources: AgentResources; claude_code_tools: ClaudeCodeTool[]; tools?: string[];
  /** May start subagents, and the tools a subagent may not be given (native agents). */
  subagents?: boolean; subagent_blocked?: string[] };
```

- [ ] **Step 4: The General switch**

In `services/admin-ui/app/components/AgentProfileSettings.tsx` import `SUBAGENTS_HELP` from `'../lib/tools'` and add,
right after the **Resources** `</Field>` (before **Enable agent**):

```tsx
      {draft.runtime !== 'claude_code' && <Field orientation="horizontal"><Switch id="agent-subagents" disabled={busy} checked={Boolean(draft.subagents)} onCheckedChange={subagents => change({ subagents })} /><div><FieldLabel htmlFor="agent-subagents">May start subagents</FieldLabel><FieldDescription>{SUBAGENTS_HELP}</FieldDescription></div></Field>}
```

- [ ] **Step 5: The Tools tab's Agents group**

In `services/admin-ui/app/components/AgentToolsView.tsx`:
- import `SUBAGENT_TOOL, SUBAGENTS_OFF_NOTE` from `'../lib/tools'` and `Checkbox` from `'~/components/ui/checkbox'`;
- `ToolRow` gains a `locked?: boolean` prop (`{ tool, editing, disabled, locked, onEdit, onChange }`, typed
  `locked?: boolean`), its `Switch` gets `disabled={disabled || locked}`, and under the summary line:

```tsx
        {locked && <p className="text-sm text-muted-foreground">Switched with May start subagents on the General tab.</p>}
```

- add after `ToolRow`:

```tsx
/** "A subagent may use": the agent's tools that are on (catalog and MCP), checked when run_subagent may give them. */
function SubagentTools({ tools, disabled, onChange }: { tools: AgentTool[]; disabled: boolean; onChange: (tool: string, allowed: boolean) => void }) {
  const id = useId();
  const offered = tools.filter(item => item.tool !== SUBAGENT_TOOL && item.mode === 'on');
  return <FieldSet className="px-3 pb-3"><FieldLegend variant="label">A subagent may use</FieldLegend>
    {offered.length ? offered.map(item => <Field key={item.tool} orientation="horizontal">
      <Checkbox id={`${id}-${item.tool}`} disabled={disabled} checked={item.subagent_allowed} onCheckedChange={checked => onChange(item.tool, checked === true)} />
      <FieldLabel htmlFor={`${id}-${item.tool}`}>{item.label}</FieldLabel></Field>)
      : <FieldDescription>No tools are on.</FieldDescription>}
  </FieldSet>;
}
```

- `keepChoices` keeps the touched `subagent_allowed`:

```tsx
  return fresh.map(item => { const own = mine.get(item.tool); return own ? { ...item, mode: own.mode, usage: own.usage, subagent_allowed: own.subagent_allowed } : item; });
```

- replace the `TOOL_GROUPS.map(...)` block in the card with:

```tsx
      {TOOL_GROUPS.map(([group, label]) => {
        const items = draft.filter(item => item.group === group);
        const row = (item: AgentTool) => <ToolRow key={item.tool} tool={item} disabled={busy} locked={item.tool === SUBAGENT_TOOL} editing={editing === item.tool}
          onEdit={() => setEditing(current => current === item.tool ? null : item.tool)} onChange={patch => change(item.tool, patch)} />;
        if (group === 'agents') return <FieldSet key={group}><FieldLegend>{label}</FieldLegend>{items.length
          ? <div className="flex flex-col divide-y rounded-md border">{items.map(item => <div key={item.tool}>{row(item)}
              {item.tool === SUBAGENT_TOOL && <SubagentTools tools={draft} disabled={busy} onChange={(name, allowed) => change(name, { subagent_allowed: allowed })} />}</div>)}</div>
          : <FieldDescription>{SUBAGENTS_OFF_NOTE}</FieldDescription>}</FieldSet>;
        return items.length ? <FieldSet key={group}><FieldLegend>{label}</FieldLegend><div className="flex flex-col divide-y rounded-md border">
          {items.map(row)}
        </div></FieldSet> : null;
      })}
```

- [ ] **Step 6: The wizard**

In `services/admin-ui/app/components/AgentWizard.tsx`:
- import `SUBAGENTS_HELP` from `'../lib/tools'`;
- state after `toolChoice`:

```tsx
  const [subagents, setSubagents] = useState(false);
  const [subagentBlocked, setSubagentBlocked] = useState<string[]>([]);  // tools a subagent may not be given
```

- in `generate` and `startBlank`, next to `setToolChoice(null)`, add `setSubagents(false); setSubagentBlocked([]);`;
- in `create`, the `createWizardAgent` argument gains
  `...(runtime === 'openai' ? { subagents, subagent_blocked: subagentBlocked.filter(name => chosenTools.includes(name)) } : {})`;
- in the Tools tab, the groups loop skips the agents group — `{TOOL_GROUPS.filter(([group]) => group !== 'agents').map(([group, label]) => {` —
  and after the **Follow the capabilities** button add:

```tsx
          <FieldSet><FieldLegend>Agents</FieldLegend>
            <Field orientation="horizontal"><Switch id="wizard-subagents" checked={subagents} onCheckedChange={setSubagents} />
              <FieldContent><FieldLabel htmlFor="wizard-subagents">May start subagents</FieldLabel><FieldDescription>{SUBAGENTS_HELP}</FieldDescription></FieldContent></Field>
            {subagents && <FieldSet><FieldLegend variant="label">A subagent may use</FieldLegend>
              {inCatalogOrder(chosenTools).map(name => <Field key={name} orientation="horizontal">
                <Checkbox id={`wizard-subagent-${name}`} checked={!subagentBlocked.includes(name)}
                  onCheckedChange={checked => setSubagentBlocked(current => checked === true ? current.filter(item => item !== name) : [...current, name])} />
                <FieldLabel htmlFor={`wizard-subagent-${name}`}>{catalogTools.find(tool => tool.name === name)?.label ?? name}</FieldLabel></Field>)}
              {chosenTools.length === 0 && <FieldDescription>No tools are on.</FieldDescription>}</FieldSet>}
          </FieldSet>
```

- [ ] **Step 7: Run the tests and the type check**

Run: `cd services/admin-ui && npx vitest run && npx tsc --noEmit`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add services/admin-ui/app/lib/profiles.ts services/admin-ui/app/lib/tools.ts services/admin-ui/app/lib/agentWizard.ts \
  services/admin-ui/app/components/AgentProfileSettings.tsx services/admin-ui/app/components/AgentToolsView.tsx \
  services/admin-ui/app/components/AgentWizard.tsx services/admin-ui/app/lib/__tests__/agentRequests.test.ts \
  services/admin-ui/app/components/__tests__/agentTools.test.tsx services/admin-ui/app/components/__tests__/agentTypes.test.tsx \
  services/admin-ui/app/components/__tests__/agentWizard.test.tsx
git commit -m "feat(admin-ui): May start subagents on General, the Tools tab and the wizard

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: admin-ui — subagents in the run workspace

**Files:**
- Modify: `services/admin-ui/app/lib/turns.ts`
- Modify: `services/admin-ui/app/components/RunWorkspace.tsx`
- Test: `services/admin-ui/app/lib/__tests__/turns.test.ts`, `services/admin-ui/app/components/__tests__/runWorkspace.test.tsx`

**Interfaces:**
- Consumes: Task 4's events — a `run_subagent` `tool_call` (`call_id`, `arguments.task`), subagent events with
  `payload.parent_call_id` (first `input {task}`), the parent's `run_subagent` `tool_result` (`is_error`);
  `tools.SUBAGENT_TOOL` (Task 5).
- Produces: `turns.SubagentStatus = 'working' | 'done' | 'failed' | 'stopped'`, `turns.Subagent { callId; turn;
  firstId; task; work; toolCalls; answer; status; error; usage }`, `Turn.subagents: Subagent[]` (by first event),
  `turns.SUBAGENT_STATUS_LABELS: Record<SubagentStatus, string>`. `RunWorkspace` props are unchanged; the Test tab and
  the Generate documents session page get subagents without changes.

- [ ] **Step 1: Write the failing `groupTurns` tests**

Append to `services/admin-ui/app/lib/__tests__/turns.test.ts`:

```ts
test('subagent events become subagents of their turn, not part of the main work', () => {
  const [turn] = groupTurns([
    event(1, 'input', { task: 'Compare the services.' }),
    event(1, 'tool_call', { name: 'run_subagent', call_id: 's1', summary: 'Read the worker', arguments: { task: 'Read the worker', tools: ['read_file'] } }),
    event(1, 'tool_call', { name: 'run_subagent', call_id: 's2', summary: 'Read the app', arguments: { task: 'Read the app', tools: ['read_file'] } }),
    usage(1, 100, 10),
    event(1, 'input', { task: 'Read the worker', subagent: { task: 'Read the worker' }, parent_call_id: 's1' }),
    event(1, 'input', { task: 'Read the app', subagent: { task: 'Read the app' }, parent_call_id: 's2' }),
    event(1, 'tool_call', { name: 'read_file', call_id: 'r1', summary: 'w.py', parent_call_id: 's1' }),
    event(1, 'usage', { input_tokens: 30, output_tokens: 3, total_tokens: 33, parent_call_id: 's1' }),
    event(1, 'tool_result', { name: 'read_file', call_id: 'r1', preview: 'def main', is_error: false, parent_call_id: 's1' }),
    event(1, 'message', { text: 'Worker reads w.py.', parent_call_id: 's1' }),
    event(1, 'tool_result', { name: 'run_subagent', call_id: 's1', preview: 'Worker reads w.py.', is_error: false }),
    event(1, 'error', { message: 'Subagent failed: model unavailable', parent_call_id: 's2' }),
    event(1, 'tool_result', { name: 'run_subagent', call_id: 's2', preview: 'Subagent failed: model unavailable', is_error: true }),
    event(1, 'message', { text: 'The worker reads w.py.' }), usage(1, 50, 5),
    event(1, 'end', { status: 'completed' }),
  ]);
  expect(turn.answer).toBe('The worker reads w.py.');
  expect(turn.work.map(item => item.name)).toEqual(['run_subagent', 'run_subagent']);
  expect([turn.toolCalls, turn.usage.total, turn.error]).toEqual([2, 165, null]);  // the main agent's own
  const [worker, app] = turn.subagents;
  expect(worker).toMatchObject({ callId: 's1', turn: 1, task: 'Read the worker', answer: 'Worker reads w.py.', status: 'done', toolCalls: 1, error: null });
  expect(worker.work).toMatchObject([{ kind: 'tool', name: 'read_file', summary: 'w.py', preview: 'def main', isError: false }]);
  expect(worker.usage).toMatchObject({ input: 30, output: 3, total: 33, calls: 1 });
  expect(app).toMatchObject({ callId: 's2', task: 'Read the app', status: 'failed', error: 'Subagent failed: model unavailable', answer: null });
});

test('a subagent works until its result arrives, is stopped when the turn ends first, and may start before the page', () => {
  const running = [event(2, 'input', { task: 'Watch.' }),
    event(2, 'tool_call', { name: 'run_subagent', call_id: 's9', summary: 'Watch the queue', arguments: { task: 'Watch the queue' } }),
    event(2, 'reasoning', { text: 'Polling.', parent_call_id: 's9' })];
  expect(groupTurns(running)[0].subagents).toMatchObject([{ task: 'Watch the queue', status: 'working', work: [{ kind: 'reasoning', text: 'Polling.' }] }]);
  expect(groupTurns([...running, event(2, 'end', { status: 'stopped' })])[0].subagents[0].status).toBe('stopped');
  const [late] = groupTurns([event(3, 'message', { text: 'Half done.', parent_call_id: 's3' })]);
  expect(late.subagents).toMatchObject([{ callId: 's3', task: '', answer: 'Half done.', status: 'working' }]);
  expect([late.answer, late.work]).toEqual([null, []]);
});
```

In the existing test `a page that starts in the middle of a turn, failed and stopped turns` add
`expect(turns.every(turn => turn.subagents.length === 0)).toBe(true);` as its last line.

- [ ] **Step 2: Write the failing workspace test**

Append to `services/admin-ui/app/components/__tests__/runWorkspace.test.tsx`:

```tsx
const delegated = [
  event(5, 'input', { task: 'Compare the services.' }),
  event(5, 'tool_call', { name: 'run_subagent', call_id: 's1', summary: 'Read the worker', arguments: { task: 'Read the worker' } }),
  event(5, 'input', { task: 'Read the worker', subagent: { task: 'Read the worker' }, parent_call_id: 's1' }),
  event(5, 'tool_call', { name: 'read_file', call_id: 'r1', summary: 'w.py', parent_call_id: 's1' }),
  event(5, 'usage', { input_tokens: 20000, output_tokens: 3000, total_tokens: 23000, parent_call_id: 's1' }),
  event(5, 'tool_result', { name: 'read_file', call_id: 'r1', preview: 'def main', is_error: false, parent_call_id: 's1' }),
  event(5, 'message', { text: 'Worker reads w.py.', parent_call_id: 's1' }),
];

test('a subagent is a line in the Worked block and an entry in the sidebar; Open shows its own history', () => {
  const working = { ...detail, status: 'working' as const };
  const view = show({ run: run({ session: working, events: delegated }) });
  expect(screen.getByText('Subagent · Read the worker · 1 tool call · 23k tokens · working')).toBeInTheDocument();
  const agents = within(screen.getByRole('region', { name: 'Agents in this run' }));
  const entry = agents.getByRole('button', { name: /Read the worker/ });
  expect(entry).toHaveTextContent('Working · 1 tool call · 23k tokens');
  fireEvent.click(screen.getByRole('button', { name: 'Open subagent: Read the worker' }));
  const crumbs = screen.getByRole('navigation', { name: 'breadcrumb' });
  expect(crumbs).toHaveTextContent(/Main agent\s*›\s*Subagent: Read the worker/);
  expect(screen.getByLabelText('Subagent task')).toHaveTextContent('Read the worker');
  expect(within(screen.getByLabelText('Subagent answer')).getByTestId('markdown')).toHaveTextContent('Worker reads w.py.');
  expect(screen.getByText('def main')).toBeInTheDocument();  // its own Worked block
  expect(screen.queryByLabelText('Your message')).toBeNull();
  expect(entry).toHaveAttribute('aria-pressed', 'true');
  view.rerender(<MemoryRouter><RunWorkspace run={run({ session: working, events: [...delegated,
    event(5, 'tool_result', { name: 'run_subagent', call_id: 's1', preview: 'Worker reads w.py.', is_error: false })] })}
    agentName="Payments helper" agentType="Native" resources="Fixed" composer={composer} /></MemoryRouter>);
  expect(agents.getByRole('button', { name: /Read the worker/ })).toHaveTextContent('Done · 1 tool call · 23k tokens');  // live
  expect(screen.getByRole('form', { name: 'Message box' })).toBeVisible();  // still talks to the main agent
  fireEvent.click(within(crumbs).getByRole('button', { name: 'Main agent' }));
  expect(screen.getByLabelText('Your message')).toHaveTextContent('Compare the services.');
  expect(screen.queryByLabelText('Subagent task')).toBeNull();
  fireEvent.click(agents.getByRole('button', { name: /Read the worker/ }));  // the sidebar selects too
  expect(screen.getByLabelText('Subagent task')).toHaveTextContent('Read the worker');
  fireEvent.click(agents.getByRole('button', { name: /Payments helper/ }));
  expect(screen.getByLabelText('Your message')).toBeVisible();
});
```

- [ ] **Step 3: Run them to verify they fail**

Run: `cd services/admin-ui && npx vitest run app/lib/__tests__/turns.test.ts app/components/__tests__/runWorkspace.test.tsx`
Expected: FAIL — `turn.subagents` is undefined; no subagent line.

- [ ] **Step 4: Implement `groupTurns` with subagents**

Replace `services/admin-ui/app/lib/turns.ts` with:

```ts
import type { SessionEvent } from './claudeCode';
import { SUBAGENT_TOOL } from './tools';

export type TurnStatus = 'working' | 'completed' | 'failed' | 'stopped';
export type SubagentStatus = 'working' | 'done' | 'failed' | 'stopped';
export interface WorkItem {
  id: number; kind: 'reasoning' | 'tool' | 'comment' | 'message'; text: string;
  name?: string; summary?: string; callId?: string; preview?: string; isError?: boolean;
}
export interface TurnUsage { input: number; output: number; cached: number; reasoning: number; total: number; calls: number }
export interface TurnSource { n: number; path: string; heading_path: string; anchor: string | null }
export interface TurnGrounding { sources: TurnSource[]; not_covered: boolean; reason?: string | null }
/** A helper the agent started with run_subagent (its events carry the call id as parent_call_id): its task, its own
 * work and answer, its usage and tool calls, and its status — working until the agent's run_subagent result arrives,
 * then done, or failed when that result is an error; stopped when the turn ended first. */
export interface Subagent {
  callId: string; turn: number; firstId: number; task: string; work: WorkItem[]; toolCalls: number;
  answer: string | null; status: SubagentStatus; error: string | null; usage: TurnUsage;
}
export interface Turn {
  turn: number; firstId: number; input: string | null; work: WorkItem[]; toolCalls: number;
  answer: string | null; grounding: TurnGrounding | null; status: TurnStatus; error: string | null; usage: TurnUsage;
  subagents: Subagent[];
}
export const SUBAGENT_STATUS_LABELS: Record<SubagentStatus, string> = { working: 'Working', done: 'Done', failed: 'Failed', stopped: 'Stopped' };

const ENDED: Record<string, TurnStatus> = { completed: 'completed', failed: 'failed', stopped: 'stopped' };
const text = (payload: Record<string, unknown>, key: string) => typeof payload[key] === 'string' ? payload[key] as string : '';
const amount = (payload: Record<string, unknown>, key: string) => Number(payload[key] ?? 0) || 0;
const noUsage = (): TurnUsage => ({ input: 0, output: 0, cached: 0, reasoning: 0, total: 0, calls: 0 });

type Pending = { pending: { id: number; text: string } | null };
type Work = Pending & { work: WorkItem[]; toolCalls: number; usage: TurnUsage; error: string | null };
type BuildingSubagent = Subagent & Pending;
type BuildingTurn = Turn & Pending & { helpers: Map<string, BuildingSubagent> };

function settle(target: Work) {  // a message followed by more work was not the answer
  if (target.pending) target.work.push({ id: target.pending.id, kind: 'message', text: target.pending.text });
  target.pending = null;
}

/** The kinds a turn and a subagent share: thinking, tool calls with their results, messages, usage and errors. */
function addWork(target: Work, event: SessionEvent) {
  const p = event.payload;
  switch (event.kind) {
    case 'reasoning':
      if (text(p, 'text').trim()) { settle(target); target.work.push({ id: event.id, kind: 'reasoning', text: text(p, 'text') }); }
      break;
    case 'tool_call':
      settle(target);
      target.toolCalls += 1;
      target.work.push({ id: event.id, kind: 'tool', text: '', name: text(p, 'name'), summary: text(p, 'summary'),
        callId: text(p, 'call_id') || undefined });
      break;
    case 'tool_result': {
      const callId = text(p, 'call_id');
      const call = [...target.work].reverse().find(item => item.kind === 'tool' && item.preview === undefined
        && (!callId || !item.callId || item.callId === callId));
      if (call) { call.preview = text(p, 'preview'); call.isError = p.is_error === true; }
      break;
    }
    case 'message':
      settle(target);
      target.pending = { id: event.id, text: text(p, 'text') };
      break;
    case 'usage':
      target.usage.input += amount(p, 'input_tokens'); target.usage.output += amount(p, 'output_tokens');
      target.usage.cached += amount(p, 'cached_tokens'); target.usage.reasoning += amount(p, 'reasoning_tokens');
      target.usage.total += amount(p, 'total_tokens'); target.usage.calls += 1;
      break;
    case 'error':
      target.error = text(p, 'message') || null;
      break;
  }
}

function helper(turn: BuildingTurn, callId: string, event: SessionEvent): BuildingSubagent {
  let found = turn.helpers.get(callId);
  if (!found) {
    found = { callId, turn: turn.turn, firstId: event.id, task: '', work: [], toolCalls: 0, answer: null, status: 'working',
      error: null, usage: noUsage(), pending: null };
    turn.helpers.set(callId, found);
  }
  return found;
}

function answered<T extends Pending>({ pending, ...rest }: T) {
  return { ...rest, answer: pending ? pending.text : null };
}

/** A session's events as turns, in order: your message (null when the loaded page starts inside the turn), the work
 * (thinking, tool calls with their results, comments you sent while it worked, messages written along the way), the
 * final answer — the last message with no thinking or tool call after it — with its grounding, the status (working
 * until the turn's end event), the usage of its model calls and its subagents (events with a parent_call_id, never
 * part of the main work). Pure: the same events give the same turns. */
export function groupTurns(events: SessionEvent[]): Turn[] {
  const turns = new Map<number, BuildingTurn>();
  for (const event of events) {
    let turn = turns.get(event.turn);
    if (!turn) {
      turn = { turn: event.turn, firstId: event.id, input: null, work: [], toolCalls: 0, answer: null, grounding: null,
        status: 'working', error: null, usage: noUsage(), subagents: [], helpers: new Map(), pending: null };
      turns.set(event.turn, turn);
    }
    const p = event.payload;
    const parent = text(p, 'parent_call_id');
    if (parent) {
      const sub = helper(turn, parent, event);
      if (event.kind === 'input') { if (!sub.task) sub.task = text(p, 'task'); }
      else addWork(sub, event);
      continue;
    }
    switch (event.kind) {
      case 'input':
        if (p.comment) turn.work.push({ id: event.id, kind: 'comment', text: text(p, 'task') });
        else if (turn.input === null) turn.input = text(p, 'task');
        break;
      case 'end':
        turn.status = ENDED[text(p, 'status')] ?? 'failed';
        turn.grounding = (p.grounding as TurnGrounding | undefined) ?? null;
        for (const sub of turn.helpers.values()) if (sub.status === 'working') sub.status = 'stopped';
        break;
      default: {
        addWork(turn, event);
        const callId = text(p, 'call_id');
        if (text(p, 'name') !== SUBAGENT_TOOL || !callId) break;
        if (event.kind === 'tool_call') {
          const sub = helper(turn, callId, event);
          const args = p.arguments as Record<string, unknown> | undefined;
          if (!sub.task && args && typeof args.task === 'string') sub.task = args.task;
        } else if (event.kind === 'tool_result') {
          const sub = turn.helpers.get(callId);
          if (sub) sub.status = p.is_error === true ? 'failed' : 'done';
        }
      }
    }
  }
  return [...turns.values()].sort((a, b) => a.firstId - b.firstId).map(({ helpers, ...turn }) => ({
    ...answered(turn),
    subagents: [...helpers.values()].sort((a, b) => a.firstId - b.firstId).map(sub => answered(sub)),
  }));
}

/** "999", "1.5k", "18k". */
export const compactTokens = (value: number) =>
  value < 1000 ? String(value) : `${Number((value / 1000).toFixed(value < 10000 ? 1 : 0))}k`;
```

- [ ] **Step 5: Implement the workspace parts**

In `services/admin-ui/app/components/RunWorkspace.tsx`:
- imports: `import { SUBAGENT_STATUS_LABELS, compactTokens, groupTurns, type Subagent, type Turn, type WorkItem } from '~/lib/turns';`,
  `import { SUBAGENT_TOOL } from '~/lib/tools';` and
  `import { Breadcrumb, BreadcrumbItem, BreadcrumbList, BreadcrumbPage, BreadcrumbSeparator } from '~/components/ui/breadcrumb';`;
- after `plural` add:

```tsx
const short = (value: string, length = 60) => value.length > length ? `${value.slice(0, length - 1)}…` : value;
const subagentUsage = (sub: Subagent) => `${plural(sub.toolCalls, 'tool call')} · ${compactTokens(sub.usage.total)} tokens`;
```

- replace `WorkLine` with:

```tsx
function WorkLine({ item, subagents, onOpen }: { item: WorkItem; subagents?: Map<string, Subagent>; onOpen?: (callId: string) => void }) {
  const sub = item.kind === 'tool' && item.name === SUBAGENT_TOOL && item.callId ? subagents?.get(item.callId) : undefined;
  if (sub) return <div className="flex flex-wrap items-center gap-2 text-sm">
    <span className="break-words">{`Subagent · ${short(sub.task) || 'no task yet'} · ${subagentUsage(sub)} · ${SUBAGENT_STATUS_LABELS[sub.status].toLowerCase()}`}</span>
    {onOpen && <Button type="button" size="sm" variant="outline" aria-label={`Open subagent: ${sub.task}`} onClick={() => onOpen(sub.callId)}>Open</Button>}
  </div>;
  switch (item.kind) {
    case 'reasoning': return <p className="whitespace-pre-wrap text-sm italic text-muted-foreground">{item.text}</p>;
    case 'comment': return <p className="whitespace-pre-wrap text-sm"><span className="font-medium">Your comment: </span>{item.text}</p>;
    case 'message': return <p className="whitespace-pre-wrap text-sm">{item.text}</p>;
    case 'tool': return <div className="flex flex-col gap-1 text-sm">
      <div><Badge variant="outline" className="mr-2">{item.name}</Badge><code className="break-all">{item.summary}</code></div>
      {item.preview ? <pre className={`max-h-48 overflow-auto rounded-md border p-2 text-xs${item.isError ? ' text-destructive' : ''}`}>{item.preview}</pre> : null}
    </div>;
  }
}
```

- `TurnView` gains `onOpen: (callId: string) => void` and renders its work with the turn's subagents:

```tsx
function TurnView({ turn, live, modelCalls, actions, onOpen }: { turn: Turn; live: boolean; modelCalls: boolean; actions?: ReactNode; onOpen: (callId: string) => void }) {
  const worked = turn.work.length > 0 || turn.usage.total > 0;
  const subagents = new Map(turn.subagents.map(sub => [sub.callId, sub]));
```

  and its work line becomes `{turn.work.map(item => <WorkLine key={item.id} item={item} subagents={subagents} onOpen={onOpen} />)}`;
- add after `TurnView`:

```tsx
/** One subagent's own history in the main pane: the task, its Worked block, its answer and status; live while it runs. */
function SubagentView({ sub, onBack }: { sub: Subagent; onBack: () => void }) {
  const live = sub.status === 'working';
  const worked = sub.work.length > 0 || sub.usage.total > 0;
  return <div className="flex flex-col gap-3">
    <Breadcrumb><BreadcrumbList>
      <BreadcrumbItem><Button type="button" variant="link" className="h-auto p-0" onClick={onBack}>Main agent</Button></BreadcrumbItem>
      <BreadcrumbSeparator>›</BreadcrumbSeparator>
      <BreadcrumbItem><BreadcrumbPage>Subagent: {short(sub.task)}</BreadcrumbPage></BreadcrumbItem>
    </BreadcrumbList></Breadcrumb>
    <div aria-label="Subagent task" className="self-end rounded-md bg-muted p-3 sm:max-w-[85%]">
      <p className="text-xs font-medium text-muted-foreground">Task · turn {sub.turn}</p>
      <p className="whitespace-pre-wrap break-words text-sm">{sub.task}</p></div>
    {worked && <details className="rounded-md border px-3 py-2">
      <summary className="cursor-pointer text-sm text-muted-foreground">{`${live ? 'Working' : 'Worked'} · ${subagentUsage(sub)}`}</summary>
      <div className="mt-3 flex flex-col gap-3">{sub.work.map(item => <WorkLine key={item.id} item={item} />)}</div>
    </details>}
    {sub.answer !== null && <article aria-label="Subagent answer" className="flex flex-col gap-2"><Markdown content={sub.answer} /></article>}
    {live && <p role="status" className="text-sm text-muted-foreground">Working…</p>}
    {sub.status === 'failed' && <Alert variant="destructive"><AlertDescription>{sub.error ?? 'This subagent failed.'}</AlertDescription></Alert>}
    {sub.status === 'stopped' && <p className="text-sm text-muted-foreground">Stopped.</p>}
  </div>;
}
```

- in `RunWorkspace`, after `const id = useId();`:

```tsx
  const [selected, setSelected] = useState<string | null>(null);  // the subagent shown in the main pane (its call id)
  useEffect(() => { setSelected(null); }, [session?.id]);
  const subagents = useMemo(() => turns.flatMap(turn => turn.subagents), [turns]);
  const shown = selected === null ? null : subagents.find(sub => sub.callId === selected) ?? null;
  const pick = (callId: string | null) => { setSelected(callId); setDrawer(false); };
```

- replace the `agents` section's main agent card with a list of selectable entries:

```tsx
    <section aria-label="Agents in this run" className="flex flex-col gap-2">
      <h2 className="text-sm font-semibold">Agents in this run</h2>
      <ul className="flex flex-col gap-2">
        <li><button type="button" aria-pressed={shown === null} onClick={() => pick(null)}
          className={`w-full rounded-md border p-2 text-left text-sm${shown === null ? ' border-primary' : ''}`}>
          <p className="font-medium">{agentName}</p>
          <p className="text-xs text-muted-foreground">{session
            ? `${statusLabels[session.status]} · ${plural(toolCalls, 'tool call')} · ${compactTokens(session.input_tokens + session.output_tokens)} tokens`
            : 'Not started'}</p></button></li>
        {subagents.map(sub => <li key={sub.callId}><button type="button" aria-pressed={shown?.callId === sub.callId} onClick={() => pick(sub.callId)}
          className={`w-full rounded-md border p-2 text-left text-sm${shown?.callId === sub.callId ? ' border-primary' : ''}`}>
          <p className="break-words font-medium">{short(sub.task, 48) || 'Subagent'}</p>
          <p className="text-xs text-muted-foreground">{`${SUBAGENT_STATUS_LABELS[sub.status]} · ${subagentUsage(sub)}`}</p></button></li>)}
      </ul>
    </section>
```

- in the main pane, show the selected subagent instead of the turns (paging, following and the queued line stay):

```tsx
          {shown ? <SubagentView sub={shown} onBack={() => setSelected(null)} />
            : turns.length === 0 ? empty : <ol className="flex flex-col gap-6">{turns.map((turn, index) =>
              <TurnView key={turn.turn} turn={turn} live={working && index === turns.length - 1 && turn.status === 'working'}
                modelCalls={modelCalls} actions={answerActions?.(turn)} onOpen={callId => setSelected(callId)} />)}</ol>}
```

- the component's doc comment gains: `The sidebar lists the main agent and every subagent of the loaded events; selecting
  one shows its own history in the main pane (the message box still talks to the main agent).`

- [ ] **Step 6: Run the tests and the type check**

Run: `cd services/admin-ui && npx vitest run && npx tsc --noEmit`
Expected: PASS (the existing workspace, session page and Test tab tests are unchanged).

- [ ] **Step 7: Commit**

```bash
git add services/admin-ui/app/lib/turns.ts services/admin-ui/app/components/RunWorkspace.tsx \
  services/admin-ui/app/lib/__tests__/turns.test.ts services/admin-ui/app/components/__tests__/runWorkspace.test.tsx
git commit -m "feat(admin-ui): subagents in the run workspace: Worked line, sidebar entries and their own history

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Documentation and full verification

**Files:**
- Modify: `docs/agent-profiles.md` (`## Tools`), `docs/agents.md` (`## Tools`, the run workspace paragraph),
  `docs/user-guide.md` (agent detail tabs, **Choosing tools**, **Test tab.**, configuration table, Appendix A),
  `services/admin-api/src/knowledge_admin/tools/README.md`, `deploy/README.md:46-49`

**Interfaces:**
- Consumes: the texts and limits of Global Constraints; the behaviour of Tasks 1–6.

- [ ] **Step 1: `docs/agent-profiles.md`** — in `## Tools`:
  - replace `(Code, Documents, Servers, MCP; empty groups are
hidden)` with `(Code, Documents, Servers, MCP, Agents; empty groups are hidden, except Agents)`;
  - replace `switching a capability on brings its default tools on, and tools of a server that is not
selected, or unknown tools, are refused. Step 1 offers only on and off.` with `switching a capability on switches all
of its tools on (replacing earlier off rows; `report_not_covered` only with **Answer only from documents**); a per-run
agent gets all tools of every enabled capability when it is created, switched to per run or gets a capability switched
on; agents saved before keep their tool rows. Tools of a server that is not selected, or unknown tools, are refused.`;
  - append at the end of the section:

```markdown
**Subagents.** **May start subagents** (General, native agents only, off by default; the wizard asks the same on its
Tools page) lets the agent hand separate parts of its work to helpers that run in parallel. The switch is the
`run_subagent` row in `ka.agent_tools` (`mode = 'on'`, `subagent_allowed = false`; `GET/PUT /api/agents/{id}`
`subagents`); turning it off deletes the row. While it is on, the Tools tab's **Agents** group lists **Start subagents**
(its switch follows General; **Edit usage** / **Reset** change the instructions the model reads about when to start
subagents) and **A subagent may use**: one checkbox per tool of the agent that is on (catalog and MCP tools), checked by
default (`subagent_allowed`). While it is off, the group says "Turn on May start subagents in General to let this
agent start helpers." and the Tools API refuses `run_subagent` ("Turn on May start subagents in General first.").
Clone and Apply copy the row and `subagent_allowed` with the other tool rows.
```

- [ ] **Step 2: `docs/agents.md`** — append to `## Tools` (after the tool usage counts paragraph):

```markdown
**Subagents (`run_subagent`).** An agent with **May start subagents** gets `run_subagent(task, tools, resources)`. A
subagent works on one task (at most 20,000 characters) without the conversation: its configuration is a copy of the
agent's pinned configuration with only the given tools on (names as the agent sees them; MCP servers kept only for the
given MCP tools, their other tools off; never `run_subagent`, never a tool not allowed for subagents), the resource
lists narrowed to the given ids (each must be in the agent's current lists; given resources make it a per-run run, so
an empty list gives nothing), the built-in prompt `knowledge_agents/prompts/subagent.md`, no instructions, skills off
and the same model. It gets only the turn's already decrypted credentials its configuration references and shares the
turn's scratch workspace. A turn starts at most 16 subagents ("This turn already started 16 subagents.") and runs
`AGENT_MAX_SUBAGENTS` (default 4) at once; later calls wait. The result is the subagent's final answer, redacted, or
`Subagent failed: <reason>`; refusals are tool errors naming the tool or resource. Subagents run only in native agent
sessions (Test and Generate documents). Their events go into the turn's record (`cc:<session>:<turn>`) with
`payload.parent_call_id` (the `run_subagent` call id; the first is `input {task, subagent}`), their model calls into the
session's totals and their tool calls into the tool counts. **Stop** stops the agent and every subagent; a subagent of an
ended turn starts no model or tool call.
```

  and at the end of the paragraph that starts `The Test tab and the Generate documents session page share
  \`RunWorkspace\`` add: `A \`run_subagent\` call shows in the Worked block as \`Subagent · <task> · <n> tool calls ·
  <tokens> tokens · <status>\` with **Open**; **Agents in this run** lists the main agent and every subagent (working,
  done, failed or stopped, with tokens and tool calls, live), and selecting one shows its own history in the main pane
  with the breadcrumb **Main agent › Subagent: <task>**. \`groupTurns\` puts events with a \`parent_call_id\` into the
  turn's subagents.`

- [ ] **Step 3: `docs/user-guide.md`**
  - **General** row of the agent detail tabs table: after `**Enable agent**;` insert `**May start subagents** (native
    agents: the agent can hand separate parts of its work to helpers that run in parallel);`;
  - **Tools** row: replace `(Code, Documents, Servers, MCP groups)` with `(Code, Documents, Servers, MCP, Agents
    groups)` and append ` With **May start subagents** on, the Agents group has **Start subagents** (usage, **Reset**)
    and **A subagent may use**.` before ` |` at the end of the row;
  - in **Choosing tools.** replace `new agents start with the tools their capabilities imply.` with `new agents start
    with all tools of the capabilities they use; switching a capability on switches all of its tools on, and a per-run
    agent gets all tools of the capabilities it uses when it becomes per run or gets one switched on. Agents created
    earlier keep their tools.`;
  - after the **Choosing tools.** paragraph add:

```markdown
**Subagents.** With **May start subagents** on (General, or the wizard's Tools page) a native agent can start helpers
with `run_subagent`: each gets a complete task, the tools you allow under **A subagent may use** that the agent passes
it, and some or all of the agent's resources, but not the conversation. Helpers run in parallel (`AGENT_MAX_SUBAGENTS`,
default 4, at once; at most 16 per turn) and their final answer comes back to the agent. In the Test tab and on the
session page each helper appears in the Worked block (**Open**) and under **Agents in this run**; selecting one shows
its task, its work and its answer (**Main agent** goes back). **Stop** stops the agent and all of its helpers.
```

  - in the configuration table, after the `AGENT_SESSION_MAX_SESSIONS` row add
    `| \`AGENT_MAX_SUBAGENTS\` | Subagents running at once in one native session turn (default 4; at most 16 per turn) |`;
  - in Appendix A, after the `report_not_covered` row add
    `| \`run_subagent\` | **May start subagents** on (native agents, sessions only) | Starts a helper with a subset of the agent's tools and resources and no conversation; at most 16 per turn, \`AGENT_MAX_SUBAGENTS\` at once; only the credentials its configuration needs; result redacted |`.

- [ ] **Step 4: `services/admin-api/src/knowledge_admin/tools/README.md`** — after the paragraph starting
  `` `create_agent` (`knowledge_admin/agents.py`) builds the tools`` add:

```markdown
`run_subagent` (group `agents`, needs none, never on by default) is on exactly when the agent's `run_subagent` row is
(**May start subagents**). Its run is `knowledge_agents/subagents.run_subagent`, which finds the turn's `Subagents` in
the run context (`context['subagents']`, native sessions only) and the call id in `capabilities.context.TOOL_CALL_ID`
(set by `tools/sdk.py` around each invocation). `subagent_config` checks the given tools against `context['tools']` and
`context['mcp_tools']` (the MCP tools as the model sees them, built by `agents.mcp_tool_names`) and `subagent_allowed`,
and narrows the resources to the parent's current lists.
```

- [ ] **Step 5: `deploy/README.md`** — at the end of the `agent-session-worker` bullet (after `Stop ends a turn within
  about 30 seconds.`) add: ` A turn runs at most \`AGENT_MAX_SUBAGENTS\` (default 4) subagents at once.`

- [ ] **Step 6: Check for leftovers**

Run: `grep -rn -e "capability_defaults" -e "PRIMARY" -e "accepted and ignored" services/admin-api/src`
Expected: no output.

Run: `grep -rn -e "default tools on" -e "tools their capabilities imply" docs --include=agent-profiles.md --include=user-guide.md --include=agents.md`
Expected: no output.

- [ ] **Step 7: Full verification**

Run: `bash db/tests/run.sh`
Expected: every `PASS:` line, exit 0 (no migration in this plan; it must stay green).

Run: `cd services/admin-api && uv run --frozen pytest -q`
Expected: PASS except the known failures (`test_environments` rollback, order-dependent `test_deploy` docs-folder).

Run: `cd services/admin-ui && npx vitest run && npx tsc --noEmit`
Expected: PASS.

Run: `python3 -m unittest discover -s tests`
Expected: OK.

- [ ] **Step 8: Commit**

```bash
git add docs/agent-profiles.md docs/agents.md docs/user-guide.md services/admin-api/src/knowledge_admin/tools/README.md \
  deploy/README.md
git commit -m "docs: subagents (May start subagents, run_subagent, the workspace) and the new tool defaults

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

## Self-review

**Spec coverage.**
- Tool defaults (all tools of a capability, `report_not_covered` tied to grounded, fixed agents replace off rows,
  per-run creation / switch to per run / capability on while per run, existing agents untouched, native only) → Task 1.
- The switch (General, help line, native only, off by default; the `run_subagent` row with `mode='on'`,
  `subagent_allowed=false`; profile `subagents` under the row lock with the revision bump; Tools PUT 400 while off, kept
  on; `subagent_allowed` stored; Clone and Apply copy it) → Task 2 (API), Task 5 (General, wizard).
- Tools tab and wizard (Agents group, label, editable usage with Reset, **A subagent may use**, the note while off,
  the wizard switch and checklist, the default usage text) → Tasks 2 and 5.
- The tool (catalog entry, signature, every check, 16 per turn) → Tasks 2, 3, 4.
- Running (sub-config, prompt, credentials subset, redaction, `agents.agent` with the parent's workspace, no history,
  no limits, the closed-turn filter, `SubagentEvents`, failures, the semaphore, Stop) → Tasks 3 and 4.
- Recording (same turn record, `parent_call_id`, first event, kinds unchanged, usage through `add_usage`, tool counts)
  → Task 4.
- Run workspace (`groupTurns` subagents and statuses, Worked line with **Open**, sidebar entries live, selected
  subagent with breadcrumb, message box to the main agent, the drawer) → Task 6.
- Errors table → Tasks 2 (tools PUT, Claude Code), 3 (tool, resource), 4 (16 per turn, failure).
- Testing section → Tasks 1–6; docs → Task 7.

**Contradictions with the code, and how this plan resolves them.**
- "Switch on a Claude Code agent: 400 with today's native-only message" — profile saves have no native-only message;
  the plan uses the Tools API's Claude Code message (`tools_api.CLAUDE_CODE`).
- The checks need "MCP tools as the model sees them", but the run context has only the catalog tools
  (`context['tools']`) and the model sees SDK-prefixed MCP names; `agents.agent()` now adds `context['mcp_tools']`
  (prefixed name → `mcp:<server id>:<tool>`) from the SDK's own listings.
- Catalog tools receive only the shared run context, not the tool call id that recording needs; the adapter publishes
  it in the context variable `TOOL_CALL_ID`.
- `agents.agent()` always creates its own scratch directory; it gains `workspace` (and `subagents`) so a subagent uses
  its parent's.
- A plain `Subagent failed: …` result would be recorded with `is_error = false` (`tool_failed` knew only the SDK's
  error text), so the workspace could not show "failed when the result is an error"; `tool_failed` recognises the
  prefix.
- `run_resources.resolve` narrows with database lookups; the sub-config narrows the already-resolved lists purely and
  marks a narrowed subagent `per_run` with `run_resources`, so an empty list means nothing (the existing per-run rule).
- The configuration's tool items always said `subagent_allowed: True` (`catalog.item`) and `native_tools` did not read
  the column; the column is now read and carried (and part of `sections`, so Clone/Apply compare it).
- The Tools tab hides empty groups and the wizard lists every catalog tool; the Agents group is always shown for
  native agents (with the note while off), and `run_subagent` is never a wizard tool switch or an implied tool.
- The spec gives the first subagent event both as `input {task, parent_call_id}` and as carrying `payload.subagent =
  {task}`; it carries both.
