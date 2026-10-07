# Knowledge Agents Step 1 — Unified Config and Screens Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every agent has an **Agent type** (Native or Claude Code) and the same configuration: model, prompt versions,
instructions, skills, documents, code projects, SSH, MCP, services and versions. Claude Code agents stop being
registrations. They are created with the same **New agent** wizard and edited on the same agent page with the same
tabs. **General** shows the Agent type, the model filtered by type and, for Claude Code agents, a **Claude Code tools**
card with four switches. Claude Code documentation sessions run with the agent's saved config: its active prompt,
instructions and enabled skills as the system prompt, its code projects as extra working folders, and its Claude Code
tools. Catalog tools (documents, code graph, SSH, MCP, services) are not given to Claude Code yet; each of those tabs
says so for Claude Code agents. The **Test** tab of a Claude Code agent starts a Claude Code session with that agent.

**Architecture:**
- **Schema (migration 0072 `knowledge_agents_config`):** drops `claude_code_agent_is_a_registration`; adds
  `ka.agents.claude_code_tools text[]` (default `{Read,Grep,Glob,Bash}`, values restricted) and
  `claude_code_agent_is_not_the_coordinator`. The required-prompt FK goes back to `agents(id)` for every agent (the
  generated `prompt_agent` column is dropped). Existing Claude Code agents get the built-in documentation prompt as
  their published prompt version 1, so their sessions keep the same instructions.
- **`knowledge_admin/knowledge_agents/` (new package):** `config.py` owns the agent config shape (`AgentConfig`), the
  type and model rules (`model_fits`, `CHOOSE_CLAUDE`), the Claude Code tool names (`CLAUDE_CODE_TOOLS`,
  `normalize_tools`), the guard for places that run the native runtime (`require_native`, `NATIVE_ONLY`) and the
  loader `get_agent_config` (moved from `agents.py`, which re-exports it). The loader returns one shape for both types
  with two new keys: `runtime` (always the agent's current type) and `claude_code_tools`.
- **admin-api:** the wizard (`POST /api/agents/wizard`) accepts `runtime` and `claude_code_tools`;
  `PUT /api/agents/{id}` accepts them too (switching the type needs a model that fits the new type);
  `PUT /api/agents/{id}/model` applies the type's model rule. The refusals that kept Claude Code agents out of versions,
  profile, SSH, operations and MCP settings are removed, and so are `POST/PUT /api/claude-code/agents`. Places that
  run the native runtime refuse Claude Code agents through `require_native` (test chats, live runs, the board).
- **Claude Code sessions (`claude_code_api.py`, `claude_code_worker.py`):** a new session loads the agent's config
  (active version or working copy), builds the system prompt with `claude_code.agent_system_prompt(config)`, adds the
  agent's code project folders after this run's context, defaults the model to the agent's model and stores the
  agent's Claude Code tools in the prompt snapshot. The worker maps them to `tools`, `allowed_tools` and
  `disallowed_tools`. Sessions created before this step keep all four tools.
- **admin-ui:** **All agents** has an **Agent type** column; **New agent** opens the wizard directly (no tabs) with
  an **Agent type** choice; the agent page shows the same tabs for both types, with Agent type, the model filtered by
  type, the Claude Code tools card and notes on the catalog tool tabs; the Test tab of a Claude Code agent links to
  **New documentation → With an agent** with the agent and its model preselected. `ClaudeCodeAgentForm` is removed.

**Tech Stack:**
- PostgreSQL 17 with pgvector, golang-migrate SQL migrations, `db/tests/run.sh`;
- FastAPI, pydantic v2, psycopg3 (`services/admin-api`), pytest against a disposable PostgreSQL container
  (`knowledge-agent-v3-postgres:17.10-vector0.8.6`);
- React Router 7 with shadcn/ui, Vitest with Testing Library (`services/admin-ui`).

**Spec:** `docs/superpowers/specs/2026-10-07-knowledge-agents-design.md`, **Delivery steps → 1. Unified config and
screens** (binding), plus the `knowledge_agents/config.py` part of **Code layout**. Steps 2–4 are follow-ups (see the
end of this plan).

## Global Constraints

- Implementers and reviewers never run make targets and never touch .env or .local; compose checks use a temp .env.
- Credentials never logged, stored in events or passed to Bash.
- Commit trailer: `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Python stays compatible with 3.12.
- Migration `0072_knowledge_agents_config` (`.up.sql` and `.down.sql`); `db/tests/run.sh` rollback pin `70` → `71`.
  - `ALTER TABLE agents DROP CONSTRAINT claude_code_agent_is_a_registration`.
  - `claude_code_tools text[] NOT NULL DEFAULT '{Read,Grep,Glob,Bash}' CONSTRAINT claude_code_tools_known CHECK
    (claude_code_tools <@ ARRAY['Read','Grep','Glob','Bash']::text[])`.
  - `CONSTRAINT claude_code_agent_is_not_the_coordinator CHECK (runtime = 'openai' OR NOT is_coordinator)`.
  - `agent_system_prompt_fk` becomes `FOREIGN KEY (id) REFERENCES agent_prompts(agent) DEFERRABLE INITIALLY DEFERRED`
    again; `prompt_agent` is dropped.
  - Existing Claude Code agents without a prompt get `agent_prompts(next_version=2)` and a published version 1 with
    title `Built-in documentation writer` and content equal to `knowledge_admin.claude_code.SYSTEM_PROMPT`.
  - Down refuses with `Claude Code agents have a configuration that 0070 cannot keep; delete them or reset them to a
    name and a description before rolling back 0072` when a Claude Code agent fails the 0070 registration rule, has
    tools other than all four, has configuration versions, more than one prompt version, non-empty instructions,
    server configs, SSH connection overrides or allowed servers. Otherwise it removes their prompts and empty settings
    rows and restores 0070 exactly.
- Agent type values: `openai` (UI label **Native**) and `claude_code` (UI label **Claude Code**).
- Claude Code tools: `CLAUDE_CODE_TOOLS = ('Read', 'Grep', 'Glob', 'Bash')`; `normalize_tools` keeps that order and
  removes duplicates. API input type `list[Literal['Read', 'Grep', 'Glob', 'Bash']]` with `max_length=8` (duplicates
  are allowed in the request and removed when saved).
- Model rule `model_fits(runtime, model, *, coordinator=False)`: embeddings never fit; `claude_code` →
  `knowledge_llm.claude_model(...)`; `openai` → coordinator: any other format, specialist:
  `knowledge_llm.code_compatible(...)`.
  - `CHOOSE_CLAUDE = 'Choose a Claude model for Claude Code agents'` (400) when the wizard creates, `PUT
    /api/agents/{id}` switches to, or `PUT /api/agents/{id}/model` sets a non-Claude model on a Claude Code agent.
  - Coordinator type switch: 400 `The coordinator runs on the native runtime.`
- Config snapshot (`AgentConfig`): the existing keys plus `runtime` and `claude_code_tools`. For a saved version the
  loader sets `runtime` from the agent row (the type belongs to the agent) and keeps the version's
  `claude_code_tools`, defaulting to all four for versions saved before 0072.
- Native-only guard: `NATIVE_ONLY = 'Claude Code agents run in Claude Code sessions. Open the agent’s Test tab to
  start one.'` raised as `ValueError` by `require_native(config)` (`config.get('runtime', 'openai') != 'openai'`);
  test chats and live runs answer 409 with it; board submit keeps its 400 `Claude Code agents cannot join the
  multi-agent board.`; the board run calls `require_native` for every member.
  `knowledge_llm.profiles.profile(..., runnable=True)` keeps refusing Claude Code agents.
- Claude Code system prompt `agent_system_prompt(config)`: the active prompt content (stripped); then
  `## Instructions\n\n<instructions>` when the instructions are not blank; then `## Skills\n\n` followed by one
  `### <name>\n\n<description>\n\n<instructions>` block per enabled skill (blank parts left out, blocks joined by a
  blank line) when Skills is on and at least one selected skill is enabled. Sections are joined by a blank line. It is
  appended to Claude Code's preset (`{'type': 'preset', 'preset': 'claude_code', 'append': ...}`) as today; the
  documentation prompt, comments and context stay in the first user message (`first_message`, unchanged).
- Session working folders: this run's code projects (chosen order), this run's `DATA_ROOT` folders, then the agent's
  code project folders when Code projects is on (all or selected), duplicates removed keeping the first. The
  snapshot's `code_project_ids` stay this run's choice only. An agent folder that does not exist → 400 `The folder
  <path> of the agent's code project <name> does not exist. Remove the project from the agent or check its folders.`
  No folder at all → 400 `Add at least one code project or folder as context`.
- Session model: `model_id` is optional; omitted → the agent's model; neither → 400 `Select an enabled Claude model
  (Bedrock or Anthropic) from LLM Settings.` (unchanged text).
- Session prompt snapshot: `{...documentation prompt, comments, system_prompt: agent_system_prompt(config),
  claude_code_tools, agent_version: config_version or None}`. Worker: `tools` = `allowed_tools` = the snapshot's tools
  in `CLAUDE_CODE_TOOLS` order (all four when the key is missing); `disallowed_tools` = the fixed list plus every
  Claude Code tool that is off.
- UI texts:
  - catalog tool note on Documents, SSH, MCP and Services for Claude Code agents: `Claude Code agents get these tools
    in the next update. What you save here is already part of the agent’s configuration and versions.`;
  - Code projects note: `Claude Code sessions open these code projects as read-only working folders. Code search and
    the code graph come in the next update.`;
  - Test card title `Test in a Claude Code session`, button **Start a session** →
    `/documentation/generations/new?scope=agent&agent=<id>`; disabled agents show `Enable the agent to start a
    session.` instead of the button;
  - Agent type help: Native `Runs on the OpenAI Agents SDK with the tools enabled in its tabs.`; Claude Code `Runs on
    the Claude Agent SDK (Claude Code) in its own container. Needs a Claude model (Bedrock or Anthropic).`;
  - Claude Code tools card: legend **Claude Code tools**, description `Claude Code’s own tools over the agent’s
    working folders. With all four off, the agent has only catalog tools, which come in the next update.`.
- Removed: `POST /api/claude-code/agents`, `PUT /api/claude-code/agents/{id}`, `knowledge_admin.agents.CLAUDE_CODE_ONLY`,
  `ClaudeCodeAgentForm.tsx`, `createClaudeCodeAgent`, `saveClaudeCodeAgent`, `CLAUDE_CODE_SDK`, `CLAUDE_CODE_TOOLS`
  (UI constant), the **Generic agent / Claude Code** tabs.

## Review Focus

1. **A Claude Code agent runs on the native runtime, or a native agent in a Claude Code session.** Examples: the
   loader no longer refuses Claude Code agents, so a test chat, a live run or a queued board task runs one with the
   OpenAI Agents SDK; **Test vN** on the Versions tab opens a native test chat; a native agent starts a Claude Code
   session. Pinned in:
   - Task 2: `test_claude_code_agents_stay_out_of_native_runs`;
   - Task 5: `test_session_input_is_validated` (`agent_id='search'` → 400);
   - Task 6: `tool tabs say what Claude Code gets now, and Test starts a session`.
2. **A Claude Code agent ends up with a non-Claude model.** Examples: switching the type keeps a GPT model; the model
   endpoint or the wizard accepts GPT for a Claude Code agent; a session starts with the agent's GPT model. Pinned in:
   - Task 3: `test_wizard_creates_claude_code_agents_with_claude_models_only`;
   - Task 4: `test_switching_the_type_keeps_the_config_and_needs_a_claude_model`;
   - Task 5: `test_session_uses_the_agent_prompt_instructions_skills_tools_and_code_projects` (model from the agent)
     and `test_session_input_is_validated` (`model_id=gpt` → 400).
3. **The session gets more access than the agent's config allows.** Examples: Bash still allowed after its switch is
   off; a tool outside Read/Grep/Glob/Bash; catalog credentials (SSH keys, MCP tokens, key references) copied into the
   session snapshot or events; folders of code projects the agent does not have. Pinned in:
   - Task 5: `test_claude_code_tools_switches_map_to_tools_and_allowed_tools`;
   - Task 5: `test_session_uses_the_agent_prompt_instructions_skills_tools_and_code_projects` (exact folders, no
     credential reference or key in the session).
4. **Existing Claude Code agents or sessions break on upgrade or rollback.** Examples: a registration without a
   prompt violates the restored FK; old sessions lose their tools because their snapshot has no
   `claude_code_tools`; rollback leaves orphan prompts or fails. Pinned in:
   - Task 1: `test_registrations_get_the_built_in_prompt_and_every_tool`;
   - Task 1: `test_down_refuses_configured_claude_code_agents_and_keeps_registrations`;
   - Task 5: `test_options_allow_only_read_tools_and_bash_in_the_chosen_folders` (unchanged: no key → all four).
5. **Configuration is lost or differs between the two types.** Examples: switching the type drops selections or
   instructions; the loader returns different keys per type; a version silently pins the type; the UI save omits
   `runtime` or the tools. Pinned in:
   - Task 2: `test_both_types_load_the_same_config_shape`;
   - Task 2: `test_a_version_keeps_its_tools_and_follows_the_agent_type`;
   - Task 4: `test_claude_code_agents_are_configured_like_native_agents`;
   - Task 6: `General shows the agent type, Claude models only and the Claude Code tools`.

## Decisions (where the spec leaves a choice)

- **Built-in prompt for existing Claude Code agents.** The required-prompt FK applies to every agent again, so
  Claude Code agents have prompts and prompt versions like native agents. Registrations from 0070 get
  `SYSTEM_PROMPT` (the text their sessions already used) as published version 1. Their sessions behave as before.
- **How agent instructions combine with the session prompt.** The agent's config replaces the built-in system prompt:
  active prompt + instructions + enabled skills are appended to Claude Code's preset. The documentation prompt,
  comments and context remain the first user message, exactly as today. The agent says *who it is and how it works*;
  the session says *what to write now*.
- **The agent type belongs to the agent, not to a version.** `get_agent_config` always sets `runtime` from the agent
  row, so switching the type compares the same configuration on both runtimes (the spec's goal) without creating a
  version. `claude_code_tools` is a capability and is versioned like the others.
- **Test tab for Claude Code agents = a link to New documentation → With an agent, with the agent and its model
  preselected (`?scope=agent&agent=<id>`).** Sessions in step 1 are documentation sessions (prompt, document name and
  folder are required), and step 3 moves both Test tabs onto the generalised session API. Embedding a second session
  starter now would duplicate `ClaudeCodeSessionForm` and be thrown away in step 3. The link reuses the existing
  form, session page, stream, send, stop and close.
- **Session model defaults to the agent's model.** `model_id` becomes optional on `POST /api/claude-code/sessions`;
  the form preselects the agent's model and can still override it with another Claude model.
- **The agent's code projects are added after this run's context.** The first working folder (the CLI's `cwd`) stays
  what the user chose for this run. The agent's folders are extra read-only working folders. The snapshot's
  `code_project_ids` (used to link the saved document) stay this run's choice.
- **Claude Code tools are saved with the agent details form.** The card sits on General inside the details form, so one
  **Save details** stores name, type and tools together, and `PUT /api/agents/{id}` checks the type and model rule
  in one transaction.
- **The wizard keeps the type outside `AgentProposal`.** The generation schema sent to the model is unchanged; the
  administrator chooses the type on the review General tab. `POST /api/agents/wizard` accepts `CreateInput`
  (`AgentProposal` + `runtime` + `claude_code_tools`).
- **Native-only places use one guard** (`require_native`) at the points where a config is about to run on the OpenAI
  Agents SDK, instead of refusing in the shared loader.
- **Versions tab:** Claude Code agents get versions (draft, activate, copy). **Test vN** is hidden for them until
  step 3 (`AgentVersionsView testable={false}`); sessions use the active version.

## Delivery and test boundaries

Tasks land in order on `feature/knowledge-agents`, and every suite is green after each task:
- Task 1 (migration) precedes everything: admin-api tests run every migration. Task 1 also gives the still-existing
  `POST /api/claude-code/agents` the built-in prompt so its rows satisfy the restored FK until Task 4 removes it.
- Task 2 (`knowledge_agents/config.py`) needs Task 1's column. Task 3 (wizard) and Task 4 (agent endpoints) need
  Task 2. Task 4 removes the Claude Code agent routes and moves both Claude Code test fixtures to the wizard.
- Task 5 (sessions and worker) needs Tasks 2–4.
- Tasks 6–8 (admin-ui) need the API shapes of Tasks 3–5. Task 6 changes the agent page and stops using
  `ClaudeCodeAgentForm`; Task 7 changes the list and the wizard and deletes the form.
- Task 9 updates the docs and runs every suite.

---

### Task 1: Migration 0072 — Claude Code agents take the full config

**Files:**
- Create: `db/migrations/0072_knowledge_agents_config.up.sql`
- Create: `db/migrations/0072_knowledge_agents_config.down.sql`
- Modify: `db/tests/run.sh` (rollback pin `70` → `71`)
- Modify: `services/admin-api/src/knowledge_admin/claude_code.py` (`BUILT_IN_PROMPT_TITLE`)
- Modify: `services/admin-api/src/knowledge_admin/claude_code_api.py` (`create_agent` saves the built-in prompt)
- Create: `services/admin-api/tests/test_knowledge_agents_migration.py`
- Modify: `services/admin-api/tests/test_claude_code_migration.py`
- Modify: `services/admin-api/tests/test_claude_code_agents.py`

**Interfaces:**
- Produces: `ka.agents.claude_code_tools`, checks `claude_code_tools_known` and
  `claude_code_agent_is_not_the_coordinator`, `agent_system_prompt_fk` on `id`;
  `knowledge_admin.claude_code.BUILT_IN_PROMPT_TITLE = 'Built-in documentation writer'`.

- [ ] **Step 1: Write the failing tests**

Create `services/admin-api/tests/test_knowledge_agents_migration.py`:

```python
"""0072: Claude Code agents are configured like native agents; 0070 registrations get the built-in prompt."""
from pathlib import Path

import psycopg
import pytest

from knowledge_admin.claude_code import BUILT_IN_PROMPT_TITLE, SYSTEM_PROMPT

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0072_knowledge_agents_config.up.sql', '0072_knowledge_agents_config.down.sql'
ALL_TOOLS = ['Read', 'Grep', 'Glob', 'Bash']


def run(conn, name: str):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def active_prompt(conn, agent_id: str):
    return conn.execute('''SELECT v.version,v.title,v.content,v.published_at IS NOT NULL AS published,p.next_version
        FROM ka.agent_prompts p JOIN ka.agent_prompt_versions v ON v.id=p.active_version_id WHERE p.agent=%s''',
        (agent_id,)).fetchone()


def has_column(conn, name: str) -> bool:
    return conn.execute("""SELECT 1 FROM information_schema.columns WHERE table_schema='ka' AND table_name='agents'
        AND column_name=%s""", (name,)).fetchone() is not None


def test_registrations_get_the_built_in_prompt_and_every_tool(store):
    with store.connect() as conn:
        run(conn, DOWN)
        conn.commit()
        conn.execute("INSERT INTO ka.agents(id,name,runtime) VALUES ('writer','Writer','claude_code')")
        conn.commit()
        run(conn, UP)
        conn.commit()
        assert conn.execute("SELECT runtime,claude_code_tools,model_id FROM ka.agents WHERE id='writer'").fetchone() == {
            'runtime': 'claude_code', 'claude_code_tools': ALL_TOOLS, 'model_id': None}
        assert active_prompt(conn, 'writer') == {'version': 1, 'title': BUILT_IN_PROMPT_TITLE, 'content': SYSTEM_PROMPT,
                                                 'published': True, 'next_version': 2}
        assert conn.execute("SELECT claude_code_tools FROM ka.agents WHERE id='search'").fetchone()['claude_code_tools'] == ALL_TOOLS
        assert active_prompt(conn, 'search')['title'] != BUILT_IN_PROMPT_TITLE  # native agents keep their prompts
        assert not has_column(conn, 'prompt_agent')


def test_claude_code_agents_take_every_capability_but_need_a_prompt(store):
    with store.connect() as conn:
        conn.execute("""UPDATE ka.agents SET runtime='claude_code',documents_enabled=true,documents_grounded=true,
            repositories_enabled=true,ssh_enabled=true,skills_enabled=true,mcp_enabled=true,services_enabled=true,
            all_collections=true,all_repositories=true,claude_code_tools='{Read,Grep}' WHERE id='operations'""")
        conn.commit()
        for statement, error in (
                ("UPDATE ka.agents SET claude_code_tools='{Read,Edit}' WHERE id='operations'", psycopg.errors.CheckViolation),
                ("UPDATE ka.agents SET claude_code_tools='{Read,NULL}' WHERE id='operations'", psycopg.errors.CheckViolation),
                ("UPDATE ka.agents SET claude_code_tools=NULL WHERE id='operations'", psycopg.errors.NotNullViolation),
                ("UPDATE ka.agents SET runtime='claude_code' WHERE is_coordinator", psycopg.errors.CheckViolation),
                ("UPDATE ka.agents SET runtime='other' WHERE id='operations'", psycopg.errors.CheckViolation)):
            with pytest.raises(error):
                with conn.transaction():
                    conn.execute(statement)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):  # like every agent, a Claude Code agent needs its prompt
        with store.connect() as conn:
            conn.execute("INSERT INTO ka.agents(id,name,runtime) VALUES ('no-prompt','No prompt','claude_code')")


def test_down_refuses_configured_claude_code_agents_and_keeps_registrations(store):
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET runtime='claude_code' WHERE id='search'")  # documents on: more than 0070 keeps
        conn.commit()
        with pytest.raises(psycopg.errors.RaiseException, match='Claude Code agents have a configuration'):
            run(conn, DOWN)
        conn.rollback()
        conn.execute("UPDATE ka.agents SET runtime='openai' WHERE id='search'")
        conn.commit()
        run(conn, DOWN)
        conn.commit()
        conn.execute("INSERT INTO ka.agents(id,name,runtime) VALUES ('writer','Writer','claude_code')")
        conn.commit()
        run(conn, UP)
        conn.commit()
        run(conn, DOWN)  # a registration with only its built-in prompt rolls back to a 0070 registration
        conn.commit()
        assert conn.execute("SELECT runtime,prompt_agent FROM ka.agents WHERE id='writer'").fetchone() == {
            'runtime': 'claude_code', 'prompt_agent': None}
        assert conn.execute("SELECT count(*) AS n FROM ka.agent_prompt_versions WHERE agent='writer'").fetchone()['n'] == 0
        assert not has_column(conn, 'claude_code_tools')
        with pytest.raises(psycopg.errors.CheckViolation, match='claude_code_agent_is_a_registration'):
            with conn.transaction():
                conn.execute("UPDATE ka.agents SET ssh_enabled=true WHERE id='writer'")
        run(conn, UP)
        conn.commit()
```

In `services/admin-api/tests/test_claude_code_migration.py` (the 0070 tests now run on top of 0072):
- add `LATER_UP, LATER_DOWN = '0072_knowledge_agents_config.up.sql', '0072_knowledge_agents_config.down.sql'` under
  `UP, DOWN = …`;
- give `registration` a keyword `prompt: bool = False`; when true, after the agent insert, add:

```python
    if prompt:  # from 0072 on, every agent has its own prompt
        version = uuid4()
        conn.execute('INSERT INTO ka.agent_prompts(agent,active_version_id,next_version) VALUES (%s,%s,2)', (agent_id, version))
        conn.execute("""INSERT INTO ka.agent_prompt_versions(id,agent,version,title,content,published_at)
            VALUES (%s,%s,1,'Writer','Write documentation.',now())""", (version, agent_id))
```

- `test_claude_code_agents_are_registrations_only`: start the `with` block with `run(conn, LATER_DOWN)` and
  `conn.commit()` (the rest is unchanged: it checks 0070's rules);
- `test_sessions_hold_status_folder_and_ordered_inputs`: call `registration(conn, prompt=True)`;
- `test_down_refuses_claude_code_agents_and_restores_the_prompt_rule`: start the `with` block with
  `run(conn, LATER_DOWN)` and `conn.commit()`, and after the final `run(conn, UP)` add `run(conn, LATER_UP)`.

In `services/admin-api/tests/test_claude_code_agents.py`, import `BUILT_IN_PROMPT_TITLE` from
`knowledge_admin.claude_code` and change the two assertions of
`test_claude_code_agent_is_created_listed_renamed_and_deleted`:

```python
    assert (listed['runtime'], listed['model_id'], listed['prompt_version'], listed['config_version']) == ('claude_code', None, 1, None)
    assert not any(listed[flag] for flag in FLAGS)
    detail = client.get(f'/api/agents/{agent_id}', headers=HEADERS).json()
    assert (detail['name'], detail['description'], detail['runtime'], detail['active_prompt']['title']) == (
        'Technical writer', 'Writes documentation', 'claude_code', BUILT_IN_PROMPT_TITLE)
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_knowledge_agents_migration.py tests/test_claude_code_agents.py -q`
Expected: FAIL with `ImportError: cannot import name 'BUILT_IN_PROMPT_TITLE'`.

- [ ] **Step 3: Implement**

In `services/admin-api/src/knowledge_admin/claude_code.py`, under `SYSTEM_PROMPT`, add:

```python
BUILT_IN_PROMPT_TITLE = 'Built-in documentation writer'  # prompt version 1 of agents created before 0072
```

Create `db/migrations/0072_knowledge_agents_config.up.sql`:

```sql
SET search_path = ka, public;

-- Claude Code agents are configured like native agents; only the runtime differs (0070 kept them as registrations).
ALTER TABLE agents DROP CONSTRAINT claude_code_agent_is_a_registration;
ALTER TABLE agents ADD CONSTRAINT claude_code_agent_is_not_the_coordinator CHECK (runtime = 'openai' OR NOT is_coordinator);
-- Claude Code's own tools over the working folders; with all four off the agent has only catalog tools.
ALTER TABLE agents ADD COLUMN claude_code_tools text[] NOT NULL DEFAULT '{Read,Grep,Glob,Bash}'
    CONSTRAINT claude_code_tools_known CHECK (claude_code_tools <@ ARRAY['Read','Grep','Glob','Bash']::text[]);

-- Every agent has its own system prompt again. Registrations get the built-in documentation prompt their sessions
-- used so far, as published version 1.
ALTER TABLE agents DROP CONSTRAINT agent_system_prompt_fk;
ALTER TABLE agents DROP COLUMN prompt_agent;
WITH added AS MATERIALIZED (
    SELECT a.id AS agent, gen_random_uuid() AS version_id FROM agents a
    WHERE a.runtime = 'claude_code' AND NOT EXISTS (SELECT 1 FROM agent_prompts p WHERE p.agent = a.id)),
prompts AS (INSERT INTO agent_prompts(agent, active_version_id, next_version) SELECT agent, version_id, 2 FROM added)
INSERT INTO agent_prompt_versions(id, agent, version, title, content, published_at)
SELECT version_id, agent, 1, 'Built-in documentation writer',
    'You are an expert technical writer. You explore the provided folders and context to write accurate documentation; everything you state must be grounded in what you read.',
    now() FROM added;
-- Check the new prompt rows now, so no deferred event is pending when the FK below is added.
SET CONSTRAINTS ka.active_prompt_version_fk, ka.agent_prompt_versions_agent_fkey IMMEDIATE;
SET CONSTRAINTS ka.active_prompt_version_fk, ka.agent_prompt_versions_agent_fkey DEFERRED;
ALTER TABLE agents ADD CONSTRAINT agent_system_prompt_fk FOREIGN KEY (id) REFERENCES agent_prompts(agent)
    DEFERRABLE INITIALLY DEFERRED;
```

Create `db/migrations/0072_knowledge_agents_config.down.sql`:

```sql
SET search_path = ka, public;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM agents a WHERE a.runtime = 'claude_code' AND (
            a.is_coordinator OR a.model_id IS NOT NULL OR a.active_config_version_id IS NOT NULL
            OR a.documents_enabled OR a.documents_grounded OR a.repositories_enabled OR a.ssh_enabled
            OR a.skills_enabled OR a.mcp_enabled OR a.services_enabled OR a.all_collections OR a.all_repositories
            OR a.claude_code_tools <> '{Read,Grep,Glob,Bash}'
            OR EXISTS (SELECT 1 FROM agent_versions v WHERE v.agent = a.id)
            OR (SELECT count(*) FROM agent_prompt_versions v WHERE v.agent = a.id) > 1
            OR EXISTS (SELECT 1 FROM agent_configs c WHERE c.agent = a.id
                       AND (c.server_id IS NOT NULL OR btrim(coalesce(c.instructions, '')) <> ''))
            OR EXISTS (SELECT 1 FROM agent_server_connections c WHERE c.agent_id = a.id)
            OR EXISTS (SELECT 1 FROM operations_servers s WHERE s.agent = a.id))) THEN
        RAISE EXCEPTION 'Claude Code agents have a configuration that 0070 cannot keep; delete them or reset them to a name and a description before rolling back 0072';
    END IF;
END $$;

-- Registrations keep only their name and description: their prompt and empty settings rows go.
ALTER TABLE agents DROP CONSTRAINT agent_system_prompt_fk;
DELETE FROM agent_configs c USING agents a WHERE c.agent = a.id AND a.runtime = 'claude_code';
DELETE FROM operations_settings s USING agents a WHERE s.agent = a.id AND a.runtime = 'claude_code';
DELETE FROM skills_agents s USING agents a WHERE s.agent_id = a.id AND a.runtime = 'claude_code';
DELETE FROM agent_collections s USING agents a WHERE s.agent_id = a.id AND a.runtime = 'claude_code';
DELETE FROM agent_repositories s USING agents a WHERE s.agent_id = a.id AND a.runtime = 'claude_code';
DELETE FROM agent_services s USING agents a WHERE s.agent_id = a.id AND a.runtime = 'claude_code';
DELETE FROM agent_prompts p USING agents a WHERE p.agent = a.id AND a.runtime = 'claude_code';
DELETE FROM agent_prompt_versions v USING agents a WHERE v.agent = a.id AND a.runtime = 'claude_code';
SET CONSTRAINTS ka.active_prompt_version_fk, ka.agent_prompt_versions_agent_fkey IMMEDIATE;
SET CONSTRAINTS ka.active_prompt_version_fk, ka.agent_prompt_versions_agent_fkey DEFERRED;

ALTER TABLE agents ADD COLUMN prompt_agent text GENERATED ALWAYS AS (CASE WHEN runtime = 'openai' THEN id END) STORED;
ALTER TABLE agents ADD CONSTRAINT agent_system_prompt_fk FOREIGN KEY (prompt_agent) REFERENCES agent_prompts(agent)
    DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE agents DROP CONSTRAINT claude_code_agent_is_not_the_coordinator;
ALTER TABLE agents DROP COLUMN claude_code_tools;
ALTER TABLE agents ADD CONSTRAINT claude_code_agent_is_a_registration CHECK (runtime = 'openai' OR (
    NOT is_coordinator AND model_id IS NULL AND active_config_version_id IS NULL
    AND NOT documents_enabled AND NOT documents_grounded AND NOT repositories_enabled AND NOT ssh_enabled
    AND NOT skills_enabled AND NOT mcp_enabled AND NOT services_enabled AND NOT all_collections AND NOT all_repositories));
```

In `db/tests/run.sh`, change `[ "$rolled_back_version" = 70 ]` to `[ "$rolled_back_version" = 71 ]`.

In `services/admin-api/src/knowledge_admin/claude_code_api.py`, import `BUILT_IN_PROMPT_TITLE` with `SYSTEM_PROMPT` from
`knowledge_admin.claude_code`. In `create_agent`, right after the `INSERT INTO ka.agents …` statement, add (this route
is removed in Task 4; until then its rows must satisfy the restored FK):

```python
                prompt_id = uuid4()
                conn.execute('INSERT INTO ka.agent_prompts(agent,active_version_id,next_version) VALUES (%s,%s,2)',
                             (agent_id, prompt_id))
                conn.execute('''INSERT INTO ka.agent_prompt_versions(id,agent,version,title,content,created_by,updated_by,
                    published_at) VALUES (%s,%s,1,%s,%s,%s,%s,now())''',
                    (prompt_id, agent_id, BUILT_IN_PROMPT_TITLE, SYSTEM_PROMPT, actor, actor))
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_knowledge_agents_migration.py tests/test_claude_code_migration.py tests/test_claude_code_agents.py tests/test_claude_code_sessions.py tests/test_profiles.py tests/test_agents.py -q`
Expected: PASS.

Run: `bash db/tests/run.sh`
Expected: every `PASS:` line and exit code 0.

- [ ] **Step 5: Commit**

```bash
git add db/migrations/0072_knowledge_agents_config.up.sql db/migrations/0072_knowledge_agents_config.down.sql \
  db/tests/run.sh services/admin-api/src/knowledge_admin/claude_code.py services/admin-api/src/knowledge_admin/claude_code_api.py \
  services/admin-api/tests/test_knowledge_agents_migration.py services/admin-api/tests/test_claude_code_migration.py \
  services/admin-api/tests/test_claude_code_agents.py
git commit -m "feat(db): Claude Code agents take the full agent config and their Claude Code tools (0072)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `knowledge_agents/config.py` — one config for both types

**Files:**
- Create: `services/admin-api/src/knowledge_admin/knowledge_agents/__init__.py`
- Create: `services/admin-api/src/knowledge_admin/knowledge_agents/config.py`
- Modify: `services/admin-api/src/knowledge_admin/agents.py` (`get_agent_config` moved; re-exported)
- Modify: `services/admin-api/src/knowledge_admin/agent_sessions.py` (`require_native` in `start_turn` and `create_session`)
- Modify: `services/admin-api/src/knowledge_admin/board.py` (`require_native` for every member at run time)
- Create: `services/admin-api/tests/test_knowledge_agents_config.py`
- Modify: `services/admin-api/tests/test_claude_code_agents.py`

**Interfaces:**
- Consumes: Task 1's `claude_code_tools`.
- Produces, in `knowledge_admin.knowledge_agents.config`: `Runtime = Literal['openai', 'claude_code']`,
  `ClaudeCodeTool = Literal['Read', 'Grep', 'Glob', 'Bash']`, `CLAUDE_CODE_TOOLS`, `CHOOSE_CLAUDE`, `NATIVE_ONLY`,
  `AgentConfig` (TypedDict), `normalize_tools(values) -> list[str]`,
  `model_fits(runtime, model, *, coordinator=False) -> bool`, `require_native(config) -> None`,
  `get_agent_config(agent_id, *, conninfo='', version_id=None, working_copy=False) -> dict` (adds `runtime` and
  `claude_code_tools`; no longer refuses Claude Code agents). `knowledge_admin.agents.get_agent_config` is the same
  function object.

- [ ] **Step 1: Write the failing tests**

Create `services/admin-api/tests/test_knowledge_agents_config.py`:

```python
"""One agent config for both agent types: the shape, the type and model rules, and the native-only guard."""
from uuid import uuid4

import pytest
from knowledge_llm import ModelUnavailable
from knowledge_llm.profiles import profile

from knowledge_admin.agents import get_agent_config as agents_loader
from knowledge_admin.knowledge_agents.config import (CLAUDE_CODE_TOOLS, NATIVE_ONLY, AgentConfig, get_agent_config,
                                                     model_fits, normalize_tools, require_native)
from test_agents import agent_mounts, saved_agent  # noqa: F401  (fixtures)
from test_repositories import HEADERS

EMPTY_CHANGES = {'system_prompt': None, 'instructions': None, 'server_instructions': []}


def test_both_types_load_the_same_config_shape(store, saved_agent):
    assert agents_loader is get_agent_config  # moved; existing imports keep working
    native = get_agent_config('operations', conninfo=store.conninfo)
    assert set(native) == AgentConfig.__required_keys__
    assert (native['runtime'], native['claude_code_tools']) == ('openai', list(CLAUDE_CODE_TOOLS))
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET runtime='claude_code',claude_code_tools='{Read,Bash}' WHERE id='operations'")
    claude = get_agent_config('operations', conninfo=store.conninfo)
    assert (claude['runtime'], claude['claude_code_tools']) == ('claude_code', ['Read', 'Bash'])
    shared = lambda config: {key: value for key, value in config.items() if key not in ('runtime', 'claude_code_tools')}
    assert shared(claude) == shared(native)  # switching the type changes only the runtime


def test_a_version_keeps_its_tools_and_follows_the_agent_type(store, saved_agent):
    client = saved_agent[0]
    source = client.get('/api/agents/operations/versions/source', headers=HEADERS).json()
    created = client.post('/api/agents/operations/versions', headers=HEADERS, json={
        'label': 'Before the switch', 'source_hash': source['source_hash'], 'changes': EMPTY_CHANGES})
    assert created.status_code == 201, created.text
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET runtime='claude_code',claude_code_tools='{Read}' WHERE id='operations'")
    version = get_agent_config('operations', conninfo=store.conninfo, version_id=created.json()['id'])
    assert (version['runtime'], version['claude_code_tools']) == ('claude_code', list(CLAUDE_CODE_TOOLS))


def test_model_rules_and_tool_names():
    claude = {'provider': 'bedrock', 'api_format': 'native', 'model': 'eu.anthropic.claude-sonnet-4-5-20250929-v1:0'}
    gpt = {'provider': 'openai', 'api_format': 'responses', 'model': 'gpt-5'}
    chat = {'provider': 'openai', 'api_format': 'chat_completions', 'model': 'llama'}
    embeddings = {'provider': 'openai', 'api_format': 'embeddings', 'model': 'text-embedding-3-small'}
    assert model_fits('claude_code', claude) and not model_fits('claude_code', gpt)
    assert model_fits('openai', claude) and model_fits('openai', gpt) and not model_fits('openai', chat)
    assert model_fits('openai', chat, coordinator=True) and not model_fits('openai', embeddings, coordinator=True)
    assert normalize_tools(['Bash', 'Read', 'Bash']) == ['Read', 'Bash'] and normalize_tools([]) == []
    require_native({'runtime': 'openai'})
    require_native({})  # sessions saved before 0072 have no runtime: native
    with pytest.raises(ValueError, match='Claude Code sessions'):
        require_native({'runtime': 'claude_code'})


def test_claude_code_agents_stay_out_of_native_runs(store, saved_agent):
    client = saved_agent[0]
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET runtime='claude_code' WHERE id='operations'")
    created = client.post('/api/agents/operations/sessions', headers=HEADERS, json={'title': 'Test', 'is_test': True})
    assert created.status_code == 409 and created.json()['error']['message'] == NATIVE_ONLY
    run = client.post('/api/agents/operations/runs', headers=HEADERS, json={'task': 'Check the queue', 'is_test': True})
    assert run.status_code == 409 and NATIVE_ONLY in run.text
    board = client.post('/api/agent-board/tasks', headers=HEADERS,
                        json={'id': str(uuid4()), 'task': 'Check the queue', 'subagent_ids': ['operations']})
    assert board.status_code == 400 and 'Claude Code agents' in board.json()['error']['message']
    with store.connect() as conn:
        with pytest.raises(ModelUnavailable):
            profile(conn, 'operations', runnable=True)
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_sessions').fetchone()['n'] == 0
```

In `services/admin-api/tests/test_claude_code_agents.py`, `test_claude_code_agents_are_hidden_from_openai_only_places`:
remove the two lines `with pytest.raises(ValueError, match='documentation sessions only'):` /
`get_agent_config(agent_id, conninfo=store.conninfo)` and the now unused `get_agent_config` import (the session
refusal on the next line stays: it now answers 409 with `NATIVE_ONLY`).

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_knowledge_agents_config.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'knowledge_admin.knowledge_agents'`.

- [ ] **Step 3: Create the package**

Create `services/admin-api/src/knowledge_admin/knowledge_agents/__init__.py`:

```python
"""Agents of both types (Native and Claude Code): one config, two runtimes. Existing modules move here step by step."""
```

Create `services/admin-api/src/knowledge_admin/knowledge_agents/config.py` with the header below, followed by the
body of `get_agent_config` moved verbatim from `agents.py` (lines `def get_agent_config…` to the final
`return jsonable_encoder({…})`) with the three changes listed after the header:

```python
"""The agent config: one shape for Native and Claude Code agents, the agent type and model rules, and loading the
resolved snapshot. Loading never decrypts credentials or starts a runtime."""
import os
from collections.abc import Iterable, Mapping
from pathlib import PurePosixPath
from typing import Any, Literal, NotRequired, TypedDict
from uuid import UUID

import psycopg
from fastapi.encoders import jsonable_encoder
from knowledge_llm import claude_model, code_compatible
from psycopg.rows import dict_row

from knowledge_admin.data_root import data_path

Runtime = Literal['openai', 'claude_code']
ClaudeCodeTool = Literal['Read', 'Grep', 'Glob', 'Bash']
CLAUDE_CODE_TOOLS: tuple[str, ...] = ('Read', 'Grep', 'Glob', 'Bash')
CHOOSE_CLAUDE = 'Choose a Claude model for Claude Code agents'
NATIVE_ONLY = 'Claude Code agents run in Claude Code sessions. Open the agent’s Test tab to start one.'


class AgentConfig(TypedDict):
    """The resolved config of one agent, the same for both types. Versions store this shape."""
    id: str
    name: str
    description: str
    when_to_use: str
    enabled: bool
    revision: int
    runtime: Runtime
    claude_code_tools: list[ClaudeCodeTool]
    model: dict[str, Any] | None
    system_prompt: dict[str, Any]
    instructions: str
    settings_revision: int | None
    documents: dict[str, Any]
    code: dict[str, Any]
    skills: dict[str, Any]
    mcp: dict[str, Any]
    ssh: dict[str, Any]
    services: dict[str, Any]
    config_version: NotRequired[dict[str, Any]]


def normalize_tools(values: Iterable[str]) -> list[str]:
    """Claude Code tools in their fixed order, each once."""
    chosen = set(values)
    return [tool for tool in CLAUDE_CODE_TOOLS if tool in chosen]


def model_fits(runtime: str, model: Mapping[str, Any], *, coordinator: bool = False) -> bool:
    """Claude Code agents need a Claude connection; native specialists a Responses or Claude connection; the
    coordinator any chat model."""
    if model['api_format'] == 'embeddings':
        return False
    if runtime == 'claude_code':
        return claude_model(model['provider'], model['api_format'], model['model'])
    return coordinator or code_compatible(model['provider'], model['api_format'], model['model'])


def require_native(config: Mapping[str, Any]) -> None:
    """Refuse a Claude Code agent where the native runtime would run it (test chats, live runs, the board)."""
    if config.get('runtime', 'openai') != 'openai':
        raise ValueError(NATIVE_ONLY)
```

Changes to the moved `get_agent_config` body:
1. Add `claude_code_tools` to the agent `SELECT` column list (after `runtime`).
2. Delete the two lines `if agent['runtime'] != 'openai':` / `raise ValueError(CLAUDE_CODE_ONLY)`.
3. In the saved-version branch, after `config['enabled'] = agent['enabled']`, add:

```python
            config['runtime'] = agent['runtime']  # the agent type belongs to the agent, never to a version
            config.setdefault('claude_code_tools', list(CLAUDE_CODE_TOOLS))  # versions saved before 0072
```

   and in the final `jsonable_encoder({...})`, after `'revision': agent['revision'],` add
   `'runtime': agent['runtime'], 'claude_code_tools': list(agent['claude_code_tools']),`.

Update its docstring's first line to `Return one JSON-compatible configuration snapshot (AgentConfig) for an agent of
either type.`

In `services/admin-api/src/knowledge_admin/agents.py`: delete `get_agent_config` and add, after the other
`knowledge_admin` imports:

```python
from knowledge_admin.knowledge_agents.config import get_agent_config  # noqa: F401  (moved; imported from here too)
```

Remove imports that only `get_agent_config` used (`PurePosixPath`, `UUID`, `dict_row`, `jsonable_encoder`, `psycopg`,
`data_path`) if nothing else in `agents.py` uses them (`rg -n "PurePosixPath|UUID|dict_row|jsonable_encoder|psycopg\.|data_path" services/admin-api/src/knowledge_admin/agents.py`).
Keep `CLAUDE_CODE_ONLY` for now: `mcp_api.py` and `operations_store.py` import it until Task 4.

- [ ] **Step 4: Guard the places that run the native runtime**

In `services/admin-api/src/knowledge_admin/agent_sessions.py`, import
`from knowledge_admin.knowledge_agents.config import require_native`, then:
- in `create_session`, make `require_native(config)` the first statement;
- in `start_turn`, inside the `try`, right after the `if value.session_id is not None: … else: config = …` block and
  before `if not config['enabled']:`, add `require_native(config)`.

In `services/admin-api/src/knowledge_admin/board.py`, import `require_native` the same way and, in the member loop,
right after `config = await asyncio.to_thread(get_agent_config, member['agent_id'], conninfo=store.conninfo)`, add
`require_native(config)` (an agent switched to Claude Code after its task was queued fails that task instead of
running natively).

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_knowledge_agents_config.py tests/test_agents.py tests/test_agent_versions.py tests/test_agent_sessions.py tests/test_agent_runs.py tests/test_board.py tests/test_claude_code_agents.py tests/test_agent_wizard.py tests/test_profiles.py -q`
Expected: PASS. If an existing test compares a whole config dict with a literal, add `'runtime': 'openai'` and
`'claude_code_tools': ['Read', 'Grep', 'Glob', 'Bash']` to that literal; change nothing else.

- [ ] **Step 6: Commit**

```bash
git add services/admin-api/src/knowledge_admin/knowledge_agents services/admin-api/src/knowledge_admin/agents.py \
  services/admin-api/src/knowledge_admin/agent_sessions.py services/admin-api/src/knowledge_admin/board.py \
  services/admin-api/tests/test_knowledge_agents_config.py services/admin-api/tests/test_claude_code_agents.py
git commit -m "feat(admin-api): knowledge_agents config loads one config shape for Native and Claude Code agents

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: The wizard creates agents of both types

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/agent_wizard.py`
- Modify: `services/admin-api/tests/test_agent_wizard.py`

**Interfaces:**
- Consumes: Task 2's `CLAUDE_CODE_TOOLS`, `ClaudeCodeTool`, `CHOOSE_CLAUDE`, `model_fits`, `normalize_tools`.
- Produces: `agent_wizard.CreateInput(AgentProposal)` with `runtime: Literal['openai', 'claude_code'] = 'openai'` and
  `claude_code_tools: list[ClaudeCodeTool]` (default all four); `POST /api/agents/wizard` takes `CreateInput`.
  `AgentProposal` and the generation schema are unchanged.

- [ ] **Step 1: Write the failing test**

Append to `services/admin-api/tests/test_agent_wizard.py`:

```python
def test_wizard_creates_claude_code_agents_with_claude_models_only(store, wizard):
    client, catalog, proposal, _ = wizard
    assert 'runtime' not in client.get('/api/agents/wizard/options', headers=HEADERS).json()['schema']['properties']
    created = client.post('/api/agents/wizard', headers=HEADERS, json={
        **proposal, 'name': 'Payments writer', 'runtime': 'claude_code', 'claude_code_tools': ['Bash', 'Read', 'Read']})
    assert created.status_code == 201, created.text
    config = get_agent_config(created.json()['id'], conninfo=store.conninfo)
    assert (config['runtime'], config['claude_code_tools'], config['system_prompt']['content'], config['instructions']) == (
        'claude_code', ['Read', 'Bash'], proposal['system_prompt'], proposal['instructions'])
    assert config['ssh']['enabled'] and config['skills']['items'] and config['code']['projects']  # saved like native
    gpt = client.post('/api/llm/models', headers=HEADERS, json={'name': 'GPT', 'provider': 'openai', 'model': 'gpt-5',
                      'api_format': 'responses', 'base_url': 'http://model.test/v1'}).json()['id']
    refused = client.post('/api/agents/wizard', headers=HEADERS, json={
        **proposal, 'name': 'GPT writer', 'model_id': gpt, 'runtime': 'claude_code'})
    assert refused.status_code == 400 and refused.json()['error']['message'] == 'Choose a Claude model for Claude Code agents'
    native = client.post('/api/agents/wizard', headers=HEADERS, json={**proposal, 'name': 'GPT helper', 'model_id': gpt})
    assert native.status_code == 201, native.text
    assert get_agent_config(native.json()['id'], conninfo=store.conninfo)['runtime'] == 'openai'
    for bad in ({'runtime': 'other'}, {'claude_code_tools': ['Edit']}, {'claude_code_tools': 'Read'}):
        assert client.post('/api/agents/wizard', headers=HEADERS, json={**proposal, 'name': 'Bad', **bad}).status_code == 400
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM ka.agents WHERE name IN ('GPT writer','Bad')").fetchone()['n'] == 0
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_agent_wizard.py::test_wizard_creates_claude_code_agents_with_claude_models_only -q`
Expected: FAIL: the create answers 400 (`extra='forbid'` rejects `runtime`).

- [ ] **Step 3: Implement**

In `services/admin-api/src/knowledge_admin/agent_wizard.py`:
- add `from typing import Literal` and
  `from knowledge_admin.knowledge_agents.config import CHOOSE_CLAUDE, CLAUDE_CODE_TOOLS, ClaudeCodeTool, model_fits, normalize_tools`;
- after `class AgentProposal`, add:

```python
class CreateInput(AgentProposal):
    """A reviewed proposal plus the agent type. The administrator chooses the type; the designer never proposes it."""
    runtime: Literal['openai', 'claude_code'] = 'openai'
    claude_code_tools: list[ClaudeCodeTool] = Field(default_factory=lambda: list(CLAUDE_CODE_TOOLS), max_length=8)
```

- change `def create(value: AgentProposal, request: Request):` to `def create(value: CreateInput, request: Request):`;
- replace `validate_resources(value, inventory(conn, project))` with:

```python
                catalog = inventory(conn, project)
                validate_resources(value, catalog)
                if value.runtime == 'claude_code' and value.model_id is not None:
                    chosen = next(m for m in catalog['models'] if str(m['id']) == str(value.model_id))
                    if not model_fits('claude_code', chosen):
                        raise HTTPException(400, CHOOSE_CLAUDE)
```

- in the `INSERT INTO ka.agents(...)` statement add the columns `runtime,claude_code_tools` after `model_id` and their
  values `value.runtime, normalize_tools(value.claude_code_tools)` after `value.model_id` (two more `%s`);
- in the audit details, change `{'agent_id':agent_id,'source':'wizard'}` to
  `{'agent_id':agent_id,'source':'wizard','runtime':value.runtime}`.

`GET /api/agents/wizard/options` and `/generate` keep using `AgentProposal`.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_agent_wizard.py tests/test_profiles.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/agent_wizard.py services/admin-api/tests/test_agent_wizard.py
git commit -m "feat(admin-api): the agent wizard creates Native and Claude Code agents

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Claude Code agents are configured exactly like native agents

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/profiles_api.py` (type, tools and model rule; refusals removed)
- Modify: `services/admin-api/src/knowledge_admin/agent_versions.py` (refusal removed)
- Modify: `services/admin-api/src/knowledge_admin/operations_store.py` (refusal removed)
- Modify: `services/admin-api/src/knowledge_admin/mcp_api.py` (refusal removed)
- Modify: `services/admin-api/src/knowledge_admin/agents.py` (`CLAUDE_CODE_ONLY` removed)
- Modify: `services/admin-api/src/knowledge_admin/claude_code_api.py` (agent routes removed)
- Rewrite: `services/admin-api/tests/test_claude_code_agents.py`
- Modify: `services/admin-api/tests/test_claude_code_sessions.py` (fixture creates the agent with the wizard)

**Interfaces:**
- Consumes: Tasks 2–3.
- Produces: `ProfileInput.runtime: Literal['openai', 'claude_code'] | None = None` and
  `ProfileInput.claude_code_tools: list[ClaudeCodeTool] | None = Field(default=None, max_length=8)` (`None` keeps the
  saved value); `runtime` and `claude_code_tools` in `GET /api/agents` and `GET /api/agents/{id}` (from `a.*`);
  test helper `test_claude_code_agents.claude_code_agent(client, name='Technical writer', **change) -> str`.
- Removes: `POST /api/claude-code/agents`, `PUT /api/claude-code/agents/{id}`, `CLAUDE_CODE_ONLY`.

- [ ] **Step 1: Write the failing tests**

Replace `services/admin-api/tests/test_claude_code_agents.py` with:

```python
"""Claude Code agents are created and configured exactly like native agents; only the runtime differs."""
from uuid import uuid4

import pytest

from knowledge_admin.claude_code import SYSTEM_PROMPT
from knowledge_admin.knowledge_agents.config import CHOOSE_CLAUDE
from test_api import _client
from test_profiles import create, update, wizard_payload
from test_repositories import HEADERS

ALL_TOOLS = ['Read', 'Grep', 'Glob', 'Bash']


def claude_code_agent(client, name: str = 'Technical writer', **change) -> str:
    """Create a Claude Code agent as New agent → Agent type: Claude Code does."""
    body = wizard_payload(name, description='', when_to_use='', system_prompt=SYSTEM_PROMPT, runtime='claude_code')
    response = client.post('/api/agents/wizard', headers=HEADERS, json={**body, **change})
    assert response.status_code == 201, response.text
    return response.json()['id']


def models(client) -> tuple[str, str]:
    claude = client.post('/api/llm/models', headers=HEADERS, json={
        'name': 'Claude', 'provider': 'bedrock', 'model': 'eu.anthropic.claude-sonnet-4-5-20250929-v1:0',
        'api_format': 'native', 'aws_region': 'eu-central-1', 'api_key': 'bedrock-secret-token'}).json()['id']
    gpt = client.post('/api/llm/models', headers=HEADERS, json={
        'name': 'GPT', 'provider': 'openai', 'model': 'gpt-5', 'api_format': 'responses',
        'base_url': 'http://model.test/v1'}).json()['id']
    return claude, gpt


@pytest.fixture
def writer(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    return client, claude_code_agent(client)


def test_claude_code_agent_is_listed_with_its_type_prompt_and_tools(writer):
    client, agent_id = writer
    listed = next(a for a in client.get('/api/agents', headers=HEADERS).json()['agents'] if a['id'] == agent_id)
    assert (listed['runtime'], listed['claude_code_tools'], listed['prompt_version']) == ('claude_code', ALL_TOOLS, 1)
    config = client.get(f'/api/agents/{agent_id}/config', headers=HEADERS)
    assert config.status_code == 200 and config.json()['system_prompt']['content'] == SYSTEM_PROMPT
    # One way to create and edit agents: the registration routes are gone.
    assert client.post('/api/claude-code/agents', headers=HEADERS, json={'name': 'Other'}).status_code in (404, 405)
    assert client.put(f'/api/claude-code/agents/{agent_id}', headers=HEADERS, json={'name': 'Other'}).status_code in (404, 405)


def test_claude_code_agents_are_configured_like_native_agents(store, writer):
    from test_operations import setup
    client, agent_id = writer
    server, _ = setup(client)
    claude, _ = models(client)
    profile = client.get(f'/api/agents/{agent_id}', headers=HEADERS).json()
    saved = client.put(f'/api/agents/{agent_id}/model', headers=HEADERS, json={'revision': profile['revision'], 'model_id': claude})
    assert saved.status_code == 200, saved.text
    saved = update(client, saved.json(), ssh_enabled=True, skills_enabled=True, mcp_enabled=True,
                   claude_code_tools=['Bash', 'Read', 'Read'])
    assert saved.status_code == 200, saved.text
    assert (saved.json()['claude_code_tools'], saved.json()['ssh_enabled']) == (['Read', 'Bash'], True)
    settings = client.get(f'/api/operations/settings?agent_id={agent_id}', headers=HEADERS)
    assert settings.status_code == 200, settings.text
    put = client.put(f'/api/operations/settings?agent_id={agent_id}', headers=HEADERS, json={
        'revision': settings.json()['revision'], 'instructions': 'Write for operators.', 'server_ids': [server['id']], 'skill_ids': []})
    assert put.status_code == 200, put.text
    for path in (f"/api/agents/{agent_id}/servers/{server['id']}/ssh", f'/api/mcp/code?agent_id={agent_id}'):
        assert client.get(path, headers=HEADERS).status_code == 200, path
    source = client.get(f'/api/agents/{agent_id}/versions/source', headers=HEADERS)
    assert source.status_code == 200, source.text
    config = source.json()['config']
    assert (config['runtime'], config['claude_code_tools'], config['instructions']) == ('claude_code', ['Read', 'Bash'], 'Write for operators.')
    assert [s['id'] for s in config['ssh']['servers']] == [server['id']]
    draft = client.post(f'/api/agents/{agent_id}/versions', headers=HEADERS, json={
        'label': 'First', 'source_hash': source.json()['source_hash'],
        'changes': {'system_prompt': None, 'instructions': None, 'server_instructions': []}})
    assert draft.status_code == 201, draft.text


def test_switching_the_type_keeps_the_config_and_needs_a_claude_model(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    claude, gpt = models(client)
    native = create(client, 'Payments helper')
    with_gpt = client.put(f"/api/agents/{native['id']}/model", headers=HEADERS,
                          json={'revision': native['revision'], 'model_id': gpt}).json()
    refused = update(client, with_gpt, runtime='claude_code')
    assert refused.status_code == 400 and refused.json()['error']['message'] == CHOOSE_CLAUDE
    with_claude = client.put(f"/api/agents/{native['id']}/model", headers=HEADERS,
                             json={'revision': with_gpt['revision'], 'model_id': claude}).json()
    switched = update(client, with_claude, runtime='claude_code', description='Same agent, other runtime')
    assert switched.status_code == 200, switched.text
    assert (switched.json()['runtime'], switched.json()['description'], switched.json()['active_prompt']['title']) == (
        'claude_code', 'Same agent, other runtime', native['active_prompt']['title'])
    model = client.put(f"/api/agents/{native['id']}/model", headers=HEADERS,
                       json={'revision': switched.json()['revision'], 'model_id': gpt})
    assert model.status_code == 400 and model.json()['error']['message'] == CHOOSE_CLAUDE
    back = update(client, switched.json(), runtime='openai')
    assert back.status_code == 200 and (back.json()['runtime'], back.json()['model_id']) == ('openai', claude)
    coordinator = client.get('/api/agents/coordinator', headers=HEADERS).json()
    refused = update(client, coordinator, runtime='claude_code')
    assert refused.status_code == 400 and 'native runtime' in refused.json()['error']['message']
    for bad in ({'runtime': 'other'}, {'claude_code_tools': ['Edit']}):
        assert update(client, back.json(), **bad).status_code == 400, bad


def test_claude_code_agent_with_retained_sessions_cannot_be_deleted(store, writer):
    client, agent_id = writer
    _, gpt = models(client)
    with store.connect() as conn:
        conn.execute('''INSERT INTO ka.agent_sessions_cc(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,
            context_snapshot,document_name,folder) SELECT %s,p.id,%s,(SELECT id FROM ka.principals LIMIT 1),%s,'Payments',
            '{}','{}','Payments','payments' FROM ka.projects p WHERE p.archived_at IS NULL''', (uuid4(), agent_id, gpt))
    detail = client.get(f'/api/agents/{agent_id}', headers=HEADERS).json()
    deleted = client.request('DELETE', f'/api/agents/{agent_id}', headers=HEADERS,
                             json={'revision': detail['revision'], 'confirmed': True})
    assert deleted.status_code == 409 and 'retained sessions' in deleted.json()['error']['message']
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_sessions_cc WHERE agent_id=%s', (agent_id,)).fetchone()['n'] == 1


def test_claude_code_agent_without_sessions_is_deleted_with_its_prompt(store, writer):
    client, agent_id = writer
    detail = client.get(f'/api/agents/{agent_id}', headers=HEADERS).json()
    deleted = client.request('DELETE', f'/api/agents/{agent_id}', headers=HEADERS,
                             json={'revision': detail['revision'], 'confirmed': True})
    assert deleted.status_code == 204, deleted.text
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_prompt_versions WHERE agent=%s', (agent_id,)).fetchone()['n'] == 0
```

In `services/admin-api/tests/test_claude_code_sessions.py`, add `from test_claude_code_agents import claude_code_agent`
and in the `sessions` fixture replace the line
`agent = client.post('/api/claude-code/agents', headers=HEADERS, json={'name': 'Technical writer'}).json()['id']` with
`agent = claude_code_agent(client)`.

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_claude_code_agents.py -q`
Expected: FAIL: `test_claude_code_agents_are_configured_like_native_agents` gets 409 from `PUT /api/agents/{id}/model`
(`Claude Code agents choose their model when a session starts.`), and the registration routes still answer 201/200.

- [ ] **Step 3: Implement the agent endpoints**

In `services/admin-api/src/knowledge_admin/profiles_api.py`:
- imports: replace `from knowledge_llm import ModelUnavailable, code_compatible` with `from knowledge_llm import
  ModelUnavailable`; replace `from knowledge_admin.agents import get_agent_config` with
  `from knowledge_admin.knowledge_agents.config import CHOOSE_CLAUDE, ClaudeCodeTool, get_agent_config, model_fits, normalize_tools`;
- in `ProfileInput`, add after `services_enabled`:

```python
    runtime: Literal['openai', 'claude_code'] | None = None  # None keeps the agent type
    claude_code_tools: list[ClaudeCodeTool] | None = Field(default=None, max_length=8)  # None keeps the saved tools
```

- in `listing`, inside `if config is not None:`, add
  `row['claude_code_tools'] = config.get('claude_code_tools', row['claude_code_tools'])`;
- in `update`, delete the two lines `if old['runtime'] != 'openai':` / `raise HTTPException(409, 'Claude Code agents
  have only a name and a description; …')`, and after the `all_collections … both.` check add:

```python
                runtime = value.runtime or old['runtime']
                if runtime != old['runtime']:
                    if old['is_coordinator']:
                        raise HTTPException(400, 'The coordinator runs on the native runtime.')
                    if runtime == 'claude_code' and old['model_id'] is not None:
                        current = conn.execute('SELECT provider,api_format,model FROM ka.llm_models WHERE id=%s',
                                               (old['model_id'],)).fetchone()
                        if not model_fits('claude_code', current):
                            raise HTTPException(400, CHOOSE_CLAUDE)
                tools = normalize_tools(value.claude_code_tools) if value.claude_code_tools is not None else old['claude_code_tools']
```

  and extend the final `UPDATE ka.agents SET …` with `runtime=%s,claude_code_tools=%s` before `revision=revision+1`,
  passing `runtime, tools` before `actor` in the parameter tuple;
- in `model`, delete the two lines `if old['runtime'] != 'openai':` / `raise HTTPException(409, 'Claude Code agents
  choose their model when a session starts.')`, and replace the compatibility check with:

```python
                if selected is None or not selected['enabled'] or selected['api_format'] == 'embeddings':
                    raise HTTPException(400, 'Choose an enabled model compatible with this agent.')
                if not model_fits(old['runtime'], selected, coordinator=old['is_coordinator']):
                    raise HTTPException(400, CHOOSE_CLAUDE if old['runtime'] == 'claude_code'
                                        else 'Choose an enabled model compatible with this agent.')
```

In `services/admin-api/src/knowledge_admin/agent_versions.py`, `owner`: remove `runtime` from the `SELECT` and delete
the two lines `if row['runtime'] != 'openai':` / `raise HTTPException(409, 'Claude Code agents have no configuration
versions.')`.

In `services/admin-api/src/knowledge_admin/operations_store.py` (`OperationsStore.project`) and
`services/admin-api/src/knowledge_admin/mcp_api.py` (`code_config`): delete the
`if agent['runtime'] != 'openai': raise HTTPException(409, CLAUDE_CODE_ONLY)` lines and the
`from knowledge_admin.agents import CLAUDE_CODE_ONLY` imports.

In `services/admin-api/src/knowledge_admin/agents.py`: delete `CLAUDE_CODE_ONLY = …`.

In `services/admin-api/src/knowledge_admin/claude_code_api.py`: delete `AGENT_FIELDS`, `class AgentInput`, and the
`create_agent` and `update_agent` routes (with the Task 1 prompt insert); drop `BUILT_IN_PROMPT_TITLE` from the import;
change the module docstring to `"""Interactive Claude Code documentation sessions: create, list, detail, send, stop,
close, the event stream and saving the draft as a document."""`.

Check: `rg -n "CLAUDE_CODE_ONLY|claude-code/agents|documentation sessions only" services/admin-api` → no matches.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_claude_code_agents.py tests/test_claude_code_sessions.py tests/test_profiles.py tests/test_agent_versions.py tests/test_operations.py tests/test_mcp.py tests/test_agent_ssh.py tests/test_knowledge_agents_config.py tests/test_agent_wizard.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/profiles_api.py services/admin-api/src/knowledge_admin/agent_versions.py \
  services/admin-api/src/knowledge_admin/operations_store.py services/admin-api/src/knowledge_admin/mcp_api.py \
  services/admin-api/src/knowledge_admin/agents.py services/admin-api/src/knowledge_admin/claude_code_api.py \
  services/admin-api/tests/test_claude_code_agents.py services/admin-api/tests/test_claude_code_sessions.py
git commit -m "feat(admin-api): Claude Code agents are configured like native agents; type switching needs a fitting model

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Claude Code sessions use the agent's saved config

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/claude_code.py` (`agent_system_prompt`)
- Modify: `services/admin-api/src/knowledge_admin/claude_code_api.py` (`SessionInput`, `session_context`, `create_session`)
- Modify: `services/admin-api/src/knowledge_admin/claude_code_worker.py` (`session_tools`, `build_options`)
- Modify: `services/admin-api/tests/test_claude_code_sessions.py`
- Modify: `services/admin-api/tests/test_claude_code_worker.py`

**Interfaces:**
- Consumes: Task 2's `get_agent_config`; Task 4's `claude_code_agent` test helper.
- Produces: `claude_code.agent_system_prompt(config) -> str`; `claude_code_api.NO_CONTEXT`;
  `session_context(conn, project, value, agent_projects=None)`; `SessionInput.model_id: UUID | None = None`;
  `prompt_snapshot.claude_code_tools` and `prompt_snapshot.agent_version`;
  `claude_code_worker.session_tools(session) -> list[str]`.

- [ ] **Step 1: Write the failing tests**

Append to `services/admin-api/tests/test_claude_code_sessions.py`:

```python
def test_session_uses_the_agent_prompt_instructions_skills_tools_and_code_projects(sessions):
    client, data = sessions.client, sessions.data
    skill = client.post('/api/skills', headers=HEADERS, json={
        'name': 'reference', 'description': 'API reference style', 'instructions': 'List every endpoint with its errors.'}).json()
    agent = claude_code_agent(client, 'API writer', system_prompt='You write API references.',
                              instructions='Write for operators.', model_id=sessions.claude, skills_enabled=True,
                              skill_ids=[skill['id']], repositories_enabled=True, repository_ids=[sessions.repo],
                              claude_code_tools=['Grep', 'Read'])
    body = {key: value for key, value in sessions.body.items() if key != 'model_id'}  # the agent's model is used
    response = client.post('/api/claude-code/sessions', headers=HEADERS, json={**body, 'agent_id': agent, 'code_project_ids': []})
    assert response.status_code == 201, response.text
    session = response.json()
    assert session['model_name'] == 'Claude'
    assert session['context_snapshot']['folders'] == [
        {'path': f'{data}/jira', 'kind': 'folder', 'name': 'jira'},
        {'path': f'{data}/code/app', 'kind': 'code_project', 'name': 'Payments API', 'code_project_id': sessions.repo}]
    assert session['context_snapshot']['code_project_ids'] == []
    snapshot = session['prompt_snapshot']
    assert snapshot['system_prompt'] == ('You write API references.\n\n## Instructions\n\nWrite for operators.\n\n'
                                         '## Skills\n\n### reference\n\nAPI reference style\n\nList every endpoint with its errors.')
    assert (snapshot['claude_code_tools'], snapshot['agent_version']) == (['Read', 'Grep'], None)
    assert f'- {data}/code/app (code project Payments API)' in session['inputs'][0]['text']
    serialized = json.dumps(session)
    for secret in ('api_key_ref', 'private_key_ref', 'bearer_token_ref', 'bedrock-secret-token'):
        assert secret not in serialized
    # An agent without a model needs one in the request.
    plain = claude_code_agent(client, 'Plain writer')
    missing = client.post('/api/claude-code/sessions', headers=HEADERS, json={**body, 'agent_id': plain})
    assert missing.status_code == 400 and missing.json()['error']['message'].startswith('Select an enabled Claude model')
    # The agent's own folders must exist too.
    (data / 'code/app').rmdir()
    gone = client.post('/api/claude-code/sessions', headers=HEADERS, json={**body, 'agent_id': agent, 'code_project_ids': []})
    assert gone.status_code == 400 and "of the agent's code project Payments API does not exist" in gone.json()['error']['message']
```

`claude_code_agent` is already imported by Task 4. The existing `test_session_context_snapshot_folders_servers_and_notes` keeps passing
(its agent has the built-in prompt, no instructions, skills or code projects).

Append to `services/admin-api/tests/test_claude_code_worker.py`:

```python
def test_claude_code_tools_switches_map_to_tools_and_allowed_tools():
    chosen = worker.build_options({**SESSION, 'prompt_snapshot': {'system_prompt': 'Write.', 'claude_code_tools': ['Grep', 'Read']}},
                                  BEDROCK)
    assert chosen['tools'] == chosen['allowed_tools'] == ['Read', 'Grep']
    assert {'Glob', 'Bash', 'Edit', 'Write', 'WebFetch'} <= set(chosen['disallowed_tools'])
    none = worker.build_options({**SESSION, 'prompt_snapshot': {'system_prompt': 'Write.', 'claude_code_tools': []}}, BEDROCK)
    assert none['tools'] == none['allowed_tools'] == []
    assert {'Read', 'Grep', 'Glob', 'Bash'} <= set(none['disallowed_tools'])
    assert worker.session_tools(SESSION) == ['Read', 'Grep', 'Glob', 'Bash']  # sessions created before step 1
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_claude_code_sessions.py::test_session_uses_the_agent_prompt_instructions_skills_tools_and_code_projects tests/test_claude_code_worker.py::test_claude_code_tools_switches_map_to_tools_and_allowed_tools -q`
Expected: FAIL: the session answers 400 (`model_id` is required) and `AttributeError: module … has no attribute
'session_tools'`.

- [ ] **Step 3: Implement the system prompt**

In `services/admin-api/src/knowledge_admin/claude_code.py`, add:

```python
def agent_system_prompt(config: dict[str, Any]) -> str:
    """The agent's instructions for Claude Code, appended to its preset: the active prompt, the agent's instructions
    and its enabled skills. What to write in this session stays in the first message."""
    parts = [config['system_prompt']['content'].strip()]
    if config['instructions'].strip():
        parts.append('## Instructions\n\n' + config['instructions'].strip())
    skills = [skill for skill in config['skills']['items'] if skill['enabled']] if config['skills']['enabled'] else []
    if skills:
        blocks = ['\n\n'.join(part for part in (f"### {skill['name']}", skill['description'].strip(),
                                                skill['instructions'].strip()) if part) for skill in skills]
        parts.append('## Skills\n\n' + '\n\n'.join(blocks))
    return '\n\n'.join(parts)
```

- [ ] **Step 4: Implement the session**

In `services/admin-api/src/knowledge_admin/claude_code_api.py`:
- imports: `from knowledge_admin.claude_code import agent_system_prompt, close_idle, first_message` (drop
  `SYSTEM_PROMPT`) and `from knowledge_admin.knowledge_agents.config import get_agent_config`;
- add `NO_CONTEXT = 'Add at least one code project or folder as context'` next to `NOT_CLAUDE`;
- `SessionInput`: `model_id: UUID | None = None  # None: the agent's model`, and delete the two lines
  `if not self.code_project_ids and not self.folders:` / `raise ValueError('Add at least one code project or folder as
  context')` from `valid` (the check moves to `session_context`, after the agent's folders are added);
- `session_context`: change the signature to
  `def session_context(conn, project: UUID, value: SessionInput, agent_projects: list[dict] | None = None) -> dict:`,
  update the docstring's first sentence to `Working folders as container paths: this run's code projects (in the
  chosen order), its DATA_ROOT folders, then the agent's own code projects.`, and replace everything from
  `candidates += [{'path': str(data_path(folder)) …` up to and including the `for item in candidates:` loop with:

```python
    candidates += [{'path': str(data_path(folder)), 'kind': 'folder', 'name': folder} for folder in value.folders]
    for item in agent_projects or []:  # the agent's code projects come after this run's context; no folders adds nothing
        candidates += [{'path': path, 'kind': 'code_project', 'name': item['name'], 'code_project_id': str(item['id']),
                        'agent': True} for path in item['folder_paths']]
    folders, seen = [], set()
    for item in candidates:
        from_agent = item.pop('agent', False)
        if item['path'] in seen:
            continue
        seen.add(item['path'])
        if not Path(item['path']).is_dir():
            raise HTTPException(400, f"The folder {item['path']} of the agent's code project {item['name']} does not exist. "
                                     'Remove the project from the agent or check its folders.' if from_agent else
                                f"The folder {item['path']} does not exist. Remove it from the context.")
        inside_data_root(item['path'])
        folders.append(item)
    if not folders:
        raise HTTPException(400, NO_CONTEXT)
```

- `create_session`: replace the lines from `model = conn.execute(…` through `snapshot = jsonable_encoder(…)` with:

```python
            try:  # the agent's saved config: its active version, or its working settings before the first version
                config = get_agent_config(agent['id'], conninfo=db.conninfo)
            except (LookupError, ValueError) as exc:
                raise HTTPException(400, str(exc)) from None
            model_id = value.model_id or (config['model'] or {}).get('id')
            model = conn.execute('SELECT id,provider,model,api_format,enabled FROM ka.llm_models WHERE id=%s FOR SHARE',
                                 (model_id,)).fetchone() if model_id else None
            if model is None or not model['enabled'] or not claude_model(model['provider'], model['api_format'], model['model']):
                raise HTTPException(400, NOT_CLAUDE)
            prompt = conn.execute("""SELECT p.id,p.name,v.id AS version_id,v.version,v.content
                FROM ka.documentation_prompts p JOIN ka.documentation_prompt_versions v ON v.id=p.active_version_id
                WHERE p.id=%s AND p.project_id=%s AND p.enabled""", (value.prompt_id, project)).fetchone()
            if prompt is None:
                raise HTTPException(400, 'Select an active documentation prompt.')
            if prompt['version_id'] != value.prompt_version_id:
                raise HTTPException(409, PROMPT_CHANGED)
            context = session_context(conn, project, value, config['code']['projects'] if config['code']['enabled'] else [])
            snapshot = jsonable_encoder({**prompt, 'comments': value.comments, 'system_prompt': agent_system_prompt(config),
                                         'claude_code_tools': config['claude_code_tools'],
                                         'agent_version': config.get('config_version')})
```

  (The rest of `create_session` is unchanged.) The snapshot holds text and tool names only: no model, key reference,
  SSH or MCP data.

- [ ] **Step 5: Implement the worker's tools**

In `services/admin-api/src/knowledge_admin/claude_code_worker.py`, add above `build_options`:

```python
def session_tools(session: dict[str, Any]) -> list[str]:
    """The agent's Claude Code tools when the session started; sessions created before step 1 have all four."""
    chosen = session['prompt_snapshot'].get('claude_code_tools', list(ALLOWED_TOOLS))
    return [tool for tool in ALLOWED_TOOLS if tool in chosen]
```

and in `build_options` replace
`'tools': list(ALLOWED_TOOLS), 'allowed_tools': list(ALLOWED_TOOLS), 'disallowed_tools': list(DISALLOWED_TOOLS),` with:

```python
        'tools': tools, 'allowed_tools': list(tools),
        'disallowed_tools': [*DISALLOWED_TOOLS, *(tool for tool in ALLOWED_TOOLS if tool not in tools)],
```

after adding `tools = session_tools(session)` as the first line of the function. Update its docstring to `Keyword
arguments for ClaudeAgentOptions: the agent's Claude Code tools, the chosen folders, no limits.`

- [ ] **Step 6: Run the tests and confirm they pass**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_claude_code_sessions.py tests/test_claude_code_worker.py tests/test_claude_code_runner.py tests/test_claude_code_agents.py -q`
Expected: PASS (the existing `test_options_allow_only_read_tools_and_bash_in_the_chosen_folders` still sees all four
tools: its snapshot has no `claude_code_tools`).

- [ ] **Step 7: Commit**

```bash
git add services/admin-api/src/knowledge_admin/claude_code.py services/admin-api/src/knowledge_admin/claude_code_api.py \
  services/admin-api/src/knowledge_admin/claude_code_worker.py services/admin-api/tests/test_claude_code_sessions.py \
  services/admin-api/tests/test_claude_code_worker.py
git commit -m "feat(admin-api): Claude Code sessions run with the agent's prompt, skills, tools and code projects

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: admin-ui — the agent page has the same tabs for both types

**Files:**
- Modify: `services/admin-ui/app/lib/profiles.ts`
- Modify: `services/admin-ui/app/lib/claudeCode.ts` (`claudeModel` signature, `claudeCodeNotes`)
- Modify: `services/admin-ui/app/components/AgentDetailView.tsx`
- Modify: `services/admin-ui/app/components/AgentProfileSettings.tsx`
- Modify: `services/admin-ui/app/components/AgentModelSelection.tsx`
- Modify: `services/admin-ui/app/components/AgentVersionsView.tsx` (`testable`)
- Create: `services/admin-ui/app/components/__tests__/agentTypes.test.tsx`
- Modify: `services/admin-ui/app/components/__tests__/claudeCodeAgents.test.tsx` (remove the two agent page tests)

**Interfaces:**
- Consumes: Task 4's `runtime` and `claude_code_tools` on `GET/PUT /api/agents/{id}`.
- Produces, in `lib/profiles.ts`: `AgentRuntime`, `AGENT_TYPES`, `AGENT_TYPE_HELP`, `agentTypeLabel(runtime)`,
  `CLAUDE_CODE_TOOL_NAMES`, `ClaudeCodeTool`; `AgentProfile.runtime?: AgentRuntime`,
  `AgentProfile.claude_code_tools?: ClaudeCodeTool[]`; `saveAgent` sends both when present. In `lib/claudeCode.ts`:
  `claudeModel(model: { provider: string; api_format: string; model: string })`, `CATALOG_TOOLS_NEXT`,
  `claudeCodeNotes: Partial<Record<string, string>>`. `AgentVersionsView({ …, testable = true })`.

- [ ] **Step 1: Write the failing tests**

Create `services/admin-ui/app/components/__tests__/agentTypes.test.tsx`:

```tsx
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useParams } from 'react-router';
import AgentDetailView from '../AgentDetailView';
import * as profiles from '../../lib/profiles';
import * as llm from '../../lib/llm';
import { Toaster } from '~/components/ui/sonner';

vi.mock('../../lib/profiles', async () => ({ ...await vi.importActual('../../lib/profiles'), getAgent: vi.fn(), getAgents: vi.fn(),
  getAgentOptions: vi.fn(), saveAgent: vi.fn(), saveProfileModel: vi.fn(), deleteAgent: vi.fn() }));
vi.mock('../../lib/llm', async () => ({ ...await vi.importActual('../../lib/llm'), getLlmSettings: vi.fn() }));
vi.mock('../OperationsView', () => ({ default: ({ view }: { view: string }) => <div>{view === 'test' ? 'Native test chat' : 'Operations settings'}</div> }));
vi.mock('../PromptsView', () => ({ default: () => <div>Prompt versions</div> }));
vi.mock('../AgentInstructions', () => ({ default: () => null }));
vi.mock('../AgentVersionsView', () => ({ default: ({ testable }: { testable?: boolean }) => <div>{testable === false ? 'Versions without Test vN' : 'Versions with Test vN'}</div> }));

const writer: profiles.AgentProfile = { id: 'writer', name: 'Technical writer', description: '', when_to_use: '', enabled: true,
  is_coordinator: false, runtime: 'claude_code', claude_code_tools: ['Read', 'Grep', 'Glob', 'Bash'], documents_enabled: false,
  repositories_enabled: false, ssh_enabled: false, skills_enabled: false, mcp_enabled: false, services_enabled: false,
  documents_grounded: false, revision: 1, model_id: 'claude', all_collections: false, all_repositories: false, collection_ids: [],
  repository_ids: [], service_ids: [], skill_ids: [], active_prompt: { id: 'p', title: 'Built-in documentation writer', version: 1 } };
const connection = { base_url: null, aws_region: 'eu-central-1', has_api_key: true, revision: 1, created_at: '', updated_at: '', enabled: true };
const claude = { ...connection, id: 'claude', name: 'Claude on Bedrock', provider: 'bedrock', model: 'eu.anthropic.claude-sonnet-4-5-20250929-v1:0', api_format: 'native' } as llm.ModelProfile;
const gpt = { ...connection, id: 'gpt', name: 'GPT', provider: 'openai', model: 'gpt-5', api_format: 'responses' } as llm.ModelProfile;

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ is_admin: true })));
  vi.mocked(profiles.getAgent).mockImplementation(async id => ({ ...structuredClone(writer), id,
    runtime: id === 'search' ? 'openai' : 'claude_code', model_id: id === 'search' ? 'gpt' : 'claude' }));
  vi.mocked(profiles.getAgentOptions).mockResolvedValue({ collections: [], repositories: [], services: [], skills: [] });
  vi.mocked(profiles.saveAgent).mockImplementation(async value => ({ ...value, revision: value.revision + 1 }));
  vi.mocked(llm.getLlmSettings).mockResolvedValue({ models: [claude, gpt], assignments: { search_model_id: null, code_model_id: null, operations_model_id: null, revision: 1 } });
});
afterEach(() => vi.unstubAllGlobals());
function Detail() { const { id } = useParams(); return <AgentDetailView key={id} agentId={id!} />; }
const open = (path: string) => render(<MemoryRouter initialEntries={[path]}><Routes><Route path="/agents/:id" element={<Detail />} /></Routes><Toaster /></MemoryRouter>);
const TABS = ['General', 'Skills', 'Documents', 'Code projects', 'SSH', 'MCP', 'Services', 'Prompts', 'Versions', 'Test'];

test.each(['writer', 'search'])('both agent types show the same tabs: %s', async id => {
  open(`/agents/${id}`);
  await screen.findByRole('heading', { name: 'Technical writer', level: 2 });
  expect(screen.getAllByRole('tab').map(tab => tab.textContent)).toEqual(TABS);
});

test('General shows the agent type, Claude models only and the Claude Code tools', async () => {
  const user = userEvent.setup();
  open('/agents/writer');
  expect(await screen.findByRole('combobox', { name: 'Agent type' })).toHaveTextContent('Claude Code');
  const tools = screen.getByRole('group', { name: 'Claude Code tools' });
  expect(within(tools).getAllByRole('switch').map(item => item.getAttribute('aria-checked'))).toEqual(['true', 'true', 'true', 'true']);
  await user.click(within(tools).getByRole('switch', { name: 'Bash' }));
  await user.click(screen.getByRole('button', { name: 'Save details' }));
  await waitFor(() => expect(profiles.saveAgent).toHaveBeenCalledWith(expect.objectContaining({ runtime: 'claude_code', claude_code_tools: ['Read', 'Grep', 'Glob'] })));
  await user.click(await screen.findByRole('combobox', { name: 'Model connection' }));
  expect(screen.getByRole('option', { name: 'Claude on Bedrock' })).toBeVisible();
  expect(screen.queryByRole('option', { name: 'GPT' })).toBeNull();
});

test('a native agent shows its type, every compatible model and no Claude Code tools', async () => {
  const user = userEvent.setup();
  open('/agents/search');
  expect(await screen.findByRole('combobox', { name: 'Agent type' })).toHaveTextContent('Native');
  expect(screen.queryByRole('group', { name: 'Claude Code tools' })).toBeNull();
  await user.click(await screen.findByRole('combobox', { name: 'Model connection' }));
  expect(screen.getByRole('option', { name: 'GPT' })).toBeVisible();
  expect(screen.getByRole('option', { name: 'Claude on Bedrock' })).toBeVisible();
});

test('tool tabs say what Claude Code gets now, and Test starts a session', async () => {
  const user = userEvent.setup();
  open('/agents/writer?view=documents');
  expect(await screen.findByText(/get these tools in the next update/)).toBeVisible();
  await user.click(screen.getByRole('tab', { name: 'Code projects' }));
  expect(screen.getByText(/read-only working folders/)).toBeVisible();
  await user.click(screen.getByRole('tab', { name: 'Skills' }));
  expect(screen.queryByText(/next update/)).toBeNull();
  await user.click(screen.getByRole('tab', { name: 'Versions' }));
  expect(screen.getByText('Versions without Test vN')).toBeVisible();
  await user.click(screen.getByRole('tab', { name: 'Test' }));
  expect(screen.queryByText('Native test chat')).toBeNull();
  expect(screen.getByText('Test in a Claude Code session')).toBeVisible();
  expect(screen.getByRole('link', { name: 'Start a session' })).toHaveAttribute('href', '/documentation/generations/new?scope=agent&agent=writer');
});

test('a native agent keeps its test chat and Test vN', async () => {
  open('/agents/search?view=test');
  expect(await screen.findByText('Native test chat')).toBeInTheDocument();
  expect(screen.queryByRole('link', { name: 'Start a session' })).toBeNull();
});
```

In `services/admin-ui/app/components/__tests__/claudeCodeAgents.test.tsx`, delete the tests `a Claude Code agent page
edits only its name and description` and `a Claude Code agent page names the agent once and can disable it` (the
agent page is now the shared page; Task 7 removes the rest of this file).

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-ui && pnpm exec vitest run app/components/__tests__/agentTypes.test.tsx`
Expected: FAIL: a Claude Code agent page has no tabs (`ClaudeCodeAgentForm` branch).

- [ ] **Step 3: Implement the shared helpers**

In `services/admin-ui/app/lib/profiles.ts`, add before `AgentProfile`:

```ts
export type AgentRuntime = 'openai' | 'claude_code';
export const AGENT_TYPES: [AgentRuntime, string][] = [['openai', 'Native'], ['claude_code', 'Claude Code']];
export const agentTypeLabel = (runtime?: AgentRuntime) => runtime === 'claude_code' ? 'Claude Code' : 'Native';
export const AGENT_TYPE_HELP: Record<AgentRuntime, string> = {
  openai: 'Runs on the OpenAI Agents SDK with the tools enabled in its tabs.',
  claude_code: 'Runs on the Claude Agent SDK (Claude Code) in its own container. Needs a Claude model (Bedrock or Anthropic).',
};
export const CLAUDE_CODE_TOOL_NAMES = ['Read', 'Grep', 'Glob', 'Bash'] as const;
export type ClaudeCodeTool = typeof CLAUDE_CODE_TOOL_NAMES[number];
```

change `runtime?: 'openai' | 'claude_code';` in `AgentProfile` to `runtime?: AgentRuntime; claude_code_tools?:
ClaudeCodeTool[];`, and in `saveAgent` destructure `runtime, claude_code_tools` too and add
`...(runtime ? { runtime } : {}), ...(claude_code_tools ? { claude_code_tools } : {}),` to the JSON body.

In `services/admin-ui/app/lib/claudeCode.ts`, change `claudeModel`'s parameter type to
`(model: { provider: string; api_format: string; model: string })` and add:

```ts
export const CATALOG_TOOLS_NEXT = 'Claude Code agents get these tools in the next update. What you save here is already part of the agent’s configuration and versions.';
/** Notes on the agent page tabs whose catalog tools Claude Code agents do not have yet. */
export const claudeCodeNotes: Partial<Record<string, string>> = {
  documents: CATALOG_TOOLS_NEXT, ssh: CATALOG_TOOLS_NEXT, mcp: CATALOG_TOOLS_NEXT, services: CATALOG_TOOLS_NEXT,
  git: 'Claude Code sessions open these code projects as read-only working folders. Code search and the code graph come in the next update.',
};
```

- [ ] **Step 4: Implement the agent page**

`services/admin-ui/app/components/AgentDetailView.tsx`:
- remove the `ClaudeCodeAgentForm` and `CLAUDE_CODE_SDK` imports and the whole `profile.runtime === 'claude_code' ?
  <>…</> :` branch, so every non-coordinator agent renders the tabs;
- import `claudeCodeNotes` from `~/lib/claudeCode`, `Card, CardDescription, CardFooter, CardHeader, CardTitle` from
  `~/components/ui/card`;
- after the `sections` line add `const claudeCode = profile?.runtime === 'claude_code';` and
  `const nativeTest = view === 'test' && !claudeCode;`;
- in the heading row add `{claudeCode && <Badge variant="secondary">Claude Code</Badge>}` after the name;
- first child of `TabsContent`: `{claudeCode && claudeCodeNotes[view] && <Alert><AlertDescription>{claudeCodeNotes[view]}</AlertDescription></Alert>}`;
- `AgentVersionsView` gets `testable={!claudeCode}`;
- the OperationsView wrapper becomes
  `<div hidden={!(['general','ssh','mcp'].includes(view) || nativeTest)}><OperationsView view={nativeTest ? 'test' : 'settings'} section={view} agentId={agentId} profile={profile} unsavedProfile={profileDirty} onDirtyChange={setConfigurationDirty} available={profile.enabled} active={['general','ssh','mcp'].includes(view) || nativeTest} /></div>`;
- after it add the Claude Code Test card:

```tsx
{claudeCode && view === 'test' && <Card><CardHeader><CardTitle>Test in a Claude Code session</CardTitle>
  <CardDescription>Claude Code agents are tested in an interactive session with their saved configuration: the active prompt version, instructions, skills, Claude Code tools and code projects. Choose a prompt and context, then follow the agent live, send messages, stop it and save the draft.</CardDescription></CardHeader>
  <CardFooter>{profile.enabled ? <Button asChild><Link to={`/documentation/generations/new?scope=agent&agent=${encodeURIComponent(agentId)}`}>Start a session</Link></Button>
    : <p className="text-sm text-muted-foreground">Enable the agent to start a session.</p>}</CardFooter></Card>}
```

`services/admin-ui/app/components/AgentVersionsView.tsx`: add `testable = true` to the props
(`{ agentId, dirty, onChanged, testable = true }: { …; testable?: boolean }`) and render the **Test v…** button only
when `testable`.

`services/admin-ui/app/components/AgentProfileSettings.tsx`:
- import `Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue` from `~/components/ui/select`
  and `AGENT_TYPES, AGENT_TYPE_HELP, CLAUDE_CODE_TOOL_NAMES, type AgentRuntime` from `../lib/profiles`;
- inside the general `FieldGroup`, after **When to use this agent**, add (not for the coordinator):

```tsx
{!profile.is_coordinator && <Field><FieldLabel htmlFor="agent-type">Agent type</FieldLabel>
  <Select value={draft.runtime ?? 'openai'} disabled={busy} onValueChange={value => change({ runtime: value as AgentRuntime })}>
    <SelectTrigger id="agent-type"><SelectValue /></SelectTrigger>
    <SelectContent><SelectGroup>{AGENT_TYPES.map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectGroup></SelectContent></Select>
  <FieldDescription>{AGENT_TYPE_HELP[draft.runtime ?? 'openai']} Switching keeps every setting; only the runtime changes.</FieldDescription></Field>}
```

- right after the general `<div hidden={view !== 'general'}>…</div>`, add the tools card:

```tsx
{view === 'general' && draft.runtime === 'claude_code' && <FieldSet className="rounded-lg border p-4"><FieldLegend>Claude Code tools</FieldLegend>
  <FieldDescription>Claude Code’s own tools over the agent’s working folders. With all four off, the agent has only catalog tools, which come in the next update.</FieldDescription>
  {CLAUDE_CODE_TOOL_NAMES.map(tool => { const tools = draft.claude_code_tools ?? [...CLAUDE_CODE_TOOL_NAMES]; return <Field key={tool} orientation="horizontal">
    <Switch id={`claude-code-tool-${tool}`} disabled={busy} checked={tools.includes(tool)}
      onCheckedChange={on => change({ claude_code_tools: CLAUDE_CODE_TOOL_NAMES.filter(name => name === tool ? on : tools.includes(name)) })} />
    <FieldLabel htmlFor={`claude-code-tool-${tool}`}>{tool}</FieldLabel></Field>; })}
</FieldSet>}
```

`services/admin-ui/app/components/AgentModelSelection.tsx`: import `claudeModel` from `~/lib/claudeCode`; add
`const claudeCode = profile?.runtime === 'claude_code';` and
`const fits = (model: ModelProfile) => claudeCode ? claudeModel(model) : codeCompatible(model);` (import the
`ModelProfile` type from `../lib/llm`); use `fits(model)` instead of `codeCompatible(model)` in both the `models`
filter and the option's `disabled` expression; the field description becomes
`{claudeCode ? 'Claude Code agents use Claude connections only: Bedrock inference profiles (eu.anthropic.claude-…) or the Anthropic API.' : profile?.is_coordinator || !profile && agent === 'search' ? 'Select an enabled model with tool support.' : 'Select an enabled Responses connection or a Claude model.'}`
followed by the existing empty-list hint.

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `cd services/admin-ui && pnpm exec tsc --noEmit && pnpm exec vitest run app/components/__tests__/agentTypes.test.tsx app/components/__tests__/agentTabs.test.tsx app/components/__tests__/agentModelSelection.test.tsx app/components/__tests__/agentVersions.test.tsx app/components/__tests__/claudeCodeAgents.test.tsx`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/admin-ui/app/lib/profiles.ts services/admin-ui/app/lib/claudeCode.ts \
  services/admin-ui/app/components/AgentDetailView.tsx services/admin-ui/app/components/AgentProfileSettings.tsx \
  services/admin-ui/app/components/AgentModelSelection.tsx services/admin-ui/app/components/AgentVersionsView.tsx \
  services/admin-ui/app/components/__tests__/agentTypes.test.tsx services/admin-ui/app/components/__tests__/claudeCodeAgents.test.tsx
git commit -m "feat(admin-ui): one agent page for both types with Agent type, model by type and Claude Code tools

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: admin-ui — All agents with an Agent type column and one New agent wizard

**Files:**
- Modify: `services/admin-ui/app/components/AgentsView.tsx`
- Modify: `services/admin-ui/app/components/AgentWizard.tsx`
- Modify: `services/admin-ui/app/lib/agentWizard.ts`
- Modify: `services/admin-ui/app/lib/claudeCode.ts` (agent registration helpers removed)
- Delete: `services/admin-ui/app/components/ClaudeCodeAgentForm.tsx`
- Delete: `services/admin-ui/app/components/__tests__/claudeCodeAgents.test.tsx`
- Modify: `services/admin-ui/app/components/__tests__/agentTypes.test.tsx`
- Modify: `services/admin-ui/app/components/__tests__/agentWizard.test.tsx`
- Modify: `services/admin-ui/app/components/__tests__/agentTabs.test.tsx`

**Interfaces:**
- Consumes: Task 3's `POST /api/agents/wizard` with `runtime` and `claude_code_tools`; Task 6's helpers.
- Produces: `lib/agentWizard.ts`: `NewAgent = AgentProposal & { runtime: AgentRuntime; claude_code_tools: ClaudeCodeTool[] }`,
  `createWizardAgent(value: NewAgent)`, `api_format` on catalog models.
- Removes: `ClaudeCodeAgentForm`, `createClaudeCodeAgent`, `saveClaudeCodeAgent`, `ClaudeCodeAgent`,
  `ClaudeCodeAgentInput`, `CLAUDE_CODE_SDK`, `CLAUDE_CODE_TOOLS`.

- [ ] **Step 1: Write the failing tests**

In `services/admin-ui/app/components/__tests__/agentTypes.test.tsx`, add `import AgentsView from '../AgentsView';`,
the mock `vi.mock('../AgentWizard', () => ({ default: () => <div>Agent wizard</div> }));` and:

```tsx
test('All agents shows the agent type and New agent opens one wizard', async () => {
  const user = userEvent.setup();
  vi.mocked(profiles.getAgents).mockResolvedValue({ agents: [
    { ...writer, id: 'search', name: 'Search agent', runtime: 'openai', documents_enabled: true, model_name: 'GPT', prompt_title: 'Search', prompt_version: 2 },
    { ...writer, model_name: 'Claude on Bedrock', prompt_title: 'Built-in documentation writer', prompt_version: 1 }] });
  render(<MemoryRouter initialEntries={['/agents']}><Routes><Route path="/agents" element={<AgentsView />} /></Routes></MemoryRouter>);
  const row = (await screen.findByRole('link', { name: 'Technical writer' })).closest('tr')!;
  expect(screen.getByRole('columnheader', { name: 'Agent type' })).toBeVisible();
  expect(within(row).getByText('Claude Code')).toBeVisible();
  expect(within(row).getByText('Claude on Bedrock')).toBeVisible();
  expect(within(row).getByText('v1 · Built-in documentation writer')).toBeVisible();
  const other = screen.getByRole('link', { name: 'Search agent' }).closest('tr')!;
  expect(within(other).getByText('Native')).toBeVisible();
  expect(within(other).getByText('Documents')).toBeVisible();
  await user.type(screen.getByLabelText('Find agents'), 'claude code');
  expect(screen.queryByRole('link', { name: 'Search agent' })).toBeNull();
  await user.click(screen.getByRole('button', { name: 'New agent' }));
  expect(screen.getByText('Agent wizard')).toBeVisible();
  expect(screen.queryByRole('tab')).toBeNull();
});
```

Append to `services/admin-ui/app/components/__tests__/agentWizard.test.tsx`:

```tsx
test('the Agent type choice filters models and is sent with the agent', async () => {
  vi.mocked(api.getWizardOptions).mockResolvedValue({ ...structuredClone(options), catalog: { ...structuredClone(options.catalog), models: [
    { id: 'model', name: 'Claude', model: 'eu.anthropic.claude-test', provider: 'bedrock', api_format: 'native' },
    { id: 'gpt', name: 'GPT', model: 'gpt-5', provider: 'openai', api_format: 'responses' }] } });
  const user = userEvent.setup();
  render(<MemoryRouter><AgentWizard onClose={vi.fn()} onCreated={vi.fn()} /><Toaster /></MemoryRouter>);
  await screen.findByLabelText('What should this agent do?');
  await user.click(screen.getByRole('button', { name: 'Start blank' }));
  expect(screen.getByRole('combobox', { name: 'Agent type' })).toHaveTextContent('Native');
  await user.click(screen.getByRole('combobox', { name: 'Agent model' }));
  await user.click(screen.getByRole('option', { name: /^GPT/ }));
  await user.click(screen.getByRole('combobox', { name: 'Agent type' }));
  await user.click(screen.getByRole('option', { name: 'Claude Code' }));
  expect(screen.getByRole('combobox', { name: 'Agent model' })).toHaveTextContent('No model (choose later)');  // GPT does not fit
  await user.click(screen.getByRole('combobox', { name: 'Agent model' }));
  expect(screen.queryByRole('option', { name: /^GPT/ })).toBeNull();
  await user.click(screen.getByRole('option', { name: /^Claude/ }));
  await user.type(screen.getByLabelText('Agent name'), 'Writer');
  await user.click(screen.getByRole('tab', { name: 'Prompt' }));
  await user.type(screen.getByLabelText('System prompt'), 'Write documentation.');
  await user.click(screen.getByRole('button', { name: 'Create agent' }));
  expect(api.createWizardAgent).toHaveBeenCalledWith(expect.objectContaining({ name: 'Writer', model_id: 'model',
    runtime: 'claude_code', claude_code_tools: ['Read', 'Grep', 'Glob', 'Bash'] }));
});
```

In `agentWizard.test.tsx`, add `api_format: 'native'` to the model in `options.catalog.models` and extend the two
existing `createWizardAgent` expectations with `runtime: 'openai'` inside `expect.objectContaining({ … })`.

In `services/admin-ui/app/components/__tests__/agentTabs.test.tsx`, replace the test `agents are created only through
one New agent panel with a generic and a Claude Code tab` with:

```tsx
test('agents are created only through one New agent panel with the wizard', async () => {
  render(app('/agents'));
  const button = await screen.findByRole('button', { name: 'New agent' });
  expect(screen.queryByRole('button', { name: 'New Agent Wizard' })).toBeNull();
  expect(screen.queryByLabelText('System prompt')).toBeNull();
  fireEvent.click(button);
  const panel = screen.getByRole('region', { name: 'New agent' });
  expect(within(panel).queryByRole('tab')).toBeNull();
  expect(within(panel).getByRole('button', { name: /Generate proposal/ })).toBeInTheDocument();
});
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-ui && pnpm exec vitest run app/components/__tests__/agentTypes.test.tsx app/components/__tests__/agentWizard.test.tsx app/components/__tests__/agentTabs.test.tsx`
Expected: FAIL: no **Agent type** column, the panel still has tabs, the wizard has no **Agent type** field.

- [ ] **Step 3: Implement**

`services/admin-ui/app/lib/agentWizard.ts`: import `type AgentRuntime, type ClaudeCodeTool` from `./profiles`; add
`api_format: string` to the catalog `models` item type; add
`export type NewAgent = AgentProposal & { runtime: AgentRuntime; claude_code_tools: ClaudeCodeTool[] };` and change
`createWizardAgent` to take `(value: NewAgent)` (same request).

`services/admin-ui/app/components/AgentWizard.tsx`:
- import `AGENT_TYPES, AGENT_TYPE_HELP, CLAUDE_CODE_TOOL_NAMES, type AgentRuntime` from `../lib/profiles` and
  `claudeModel` from `~/lib/claudeCode`;
- state `const [runtime, setRuntime] = useState<AgentRuntime>('openai');`;
- after `const catalog = options?.catalog;` add
  `const agentModels = catalog?.models.filter(m => runtime !== 'claude_code' || claudeModel(m)) ?? [];` and

```tsx
  function chooseType(next: AgentRuntime) {
    setRuntime(next);
    if (next === 'claude_code' && proposal?.model_id && !catalog?.models.some(m => m.id === proposal.model_id && claudeModel(m))) edit({ model_id: null });
  }
```

- in `create`, call `createWizardAgent({ ...proposal, runtime, claude_code_tools: [...CLAUDE_CODE_TOOL_NAMES] })`;
- first field of the review **General** tab:

```tsx
<Field><FieldLabel htmlFor="wizard-agent-type">Agent type</FieldLabel><Select value={runtime} onValueChange={value => chooseType(value as AgentRuntime)}><SelectTrigger id="wizard-agent-type"><SelectValue /></SelectTrigger><SelectContent><SelectGroup>{AGENT_TYPES.map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectGroup></SelectContent></Select><FieldDescription>{AGENT_TYPE_HELP[runtime]} Every other setting is the same for both types.</FieldDescription></Field>
```

- the **Agent model** select lists `agentModels` instead of `catalog.models`.

`services/admin-ui/app/components/AgentsView.tsx`:
- remove the `ClaudeCodeAgentForm`, `Tabs…` and `CLAUDE_CODE_SDK, CLAUDE_CODE_TOOLS` imports; import
  `agentTypeLabel` from `../lib/profiles`;
- the panel becomes `{creating && <section aria-label="New agent" className="flex flex-col gap-3"><AgentWizard onClose={() => setCreating(false)} onCreated={id => navigate(`/agents/${id}`)} /></section>}`;
- the search text uses `agentTypeLabel(a.runtime)` instead of the `claude code` literal;
- add `<TableHead>Agent type</TableHead>` after `<TableHead>Agent</TableHead>` and
  `<TableCell>{agentTypeLabel(a.runtime)}</TableCell>` after the agent cell; remove the `claudeCode` badge and every
  `claudeCode ? … :` branch so capabilities, model (`a.model_name ?? 'Not configured'`) and system prompt render the
  same for both types; the empty row's `colSpan` becomes `7`.

`services/admin-ui/app/lib/claudeCode.ts`: delete `CLAUDE_CODE_SDK`, `CLAUDE_CODE_TOOLS`, `ClaudeCodeAgentInput`,
`ClaudeCodeAgent`, `createClaudeCodeAgent` and `saveClaudeCodeAgent`.

Delete `services/admin-ui/app/components/ClaudeCodeAgentForm.tsx` and
`services/admin-ui/app/components/__tests__/claudeCodeAgents.test.tsx` (`git rm`).

Check: `rg -n "ClaudeCodeAgentForm|createClaudeCodeAgent|saveClaudeCodeAgent|CLAUDE_CODE_SDK|claude-code/agents|Generic agent" services/admin-ui/app` → no matches.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd services/admin-ui && pnpm exec tsc --noEmit && pnpm exec vitest run app/components/__tests__/agentTypes.test.tsx app/components/__tests__/agentWizard.test.tsx app/components/__tests__/agentTabs.test.tsx app/components/__tests__/agentBoard.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A services/admin-ui/app/components/AgentsView.tsx services/admin-ui/app/components/AgentWizard.tsx \
  services/admin-ui/app/lib/agentWizard.ts services/admin-ui/app/lib/claudeCode.ts \
  services/admin-ui/app/components/ClaudeCodeAgentForm.tsx services/admin-ui/app/components/__tests__/claudeCodeAgents.test.tsx \
  services/admin-ui/app/components/__tests__/agentTypes.test.tsx services/admin-ui/app/components/__tests__/agentWizard.test.tsx \
  services/admin-ui/app/components/__tests__/agentTabs.test.tsx
git commit -m "feat(admin-ui): All agents shows the Agent type; New agent is one wizard with an Agent type choice

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: admin-ui — the Test tab's session starts with the agent and its model

**Files:**
- Modify: `services/admin-ui/app/components/ClaudeCodeSessionForm.tsx`
- Modify: `services/admin-ui/app/components/DocumentationWorkflowView.tsx` (`NewDocumentationWorkflow`)
- Modify: `services/admin-ui/app/lib/claudeCode.ts` (`SessionInput.model_id` optional, snapshot fields)
- Modify: `services/admin-ui/app/components/__tests__/claudeCodeSessionForm.test.tsx`

**Interfaces:**
- Consumes: Task 5's optional `model_id`; Task 6's link `?scope=agent&agent=<id>`.
- Produces: `ClaudeCodeSessionForm({ onCreated, initialAgentId = '' })`; `SessionInput.model_id?: string`;
  `SessionDetail.prompt_snapshot.claude_code_tools?: string[]` and `agent_version?: { id: string; version: number; label: string } | null`.

- [ ] **Step 1: Write the failing test**

In `services/admin-ui/app/components/__tests__/claudeCodeSessionForm.test.tsx`:
- add `{ id: 'api-writer', name: 'API writer', runtime: 'claude_code', enabled: true, model_id: 'claude' }` to the
  `/api/agents` body;
- give `setup` a second parameter `path = '/documentation/generations/new?scope=agent'` used in `initialEntries`;
- append:

```tsx
test('opening it from an agent’s Test tab preselects the agent and its model', async () => {
  const posts = setup(undefined, '/documentation/generations/new?scope=agent&agent=api-writer');
  expect(await screen.findByLabelText('Agent')).toHaveTextContent('API writer');
  expect(screen.getByLabelText('Model')).toHaveTextContent('Claude on Bedrock');
  await select('Prompt', 'Deployment and service operations');
  fireEvent.change(screen.getByLabelText('Document name'), { target: { value: 'Payments' } });
  fireEvent.click(screen.getByRole('checkbox', { name: 'Payments API' }));
  fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
  expect(await screen.findByText('Session page')).toBeVisible();
  expect(posts).toEqual([expect.objectContaining({ agent_id: 'api-writer', model_id: 'claude' })]);
});

test('choosing an agent selects its model; an unknown agent in the link is ignored', async () => {
  setup(undefined, '/documentation/generations/new?scope=agent&agent=missing');
  expect(await screen.findByLabelText('Agent')).toHaveTextContent('Select…');
  await select('Agent', 'API writer');
  expect(screen.getByLabelText('Model')).toHaveTextContent('Claude on Bedrock');
});
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-ui && pnpm exec vitest run app/components/__tests__/claudeCodeSessionForm.test.tsx`
Expected: FAIL: the Agent select shows `Select…`.

- [ ] **Step 3: Implement**

`services/admin-ui/app/components/ClaudeCodeSessionForm.tsx`:
- signature `export default function ClaudeCodeSessionForm({ onCreated, initialAgentId = '' }: { onCreated: (id: string) => void; initialAgentId?: string })`;
- `const [agentId, setAgentId] = useState(initialAgentId);`;
- in the load `then`, after computing `models` and the filtered `agents`, replace
  `if (models.length === 1) setModelId(models[0].id);` with:

```tsx
      const preset = agents.find(agent => agent.id === initialAgentId);
      if (!preset) setAgentId('');
      const agentModel = models.find(model => model.id === preset?.model_id);
      if (agentModel) setModelId(agentModel.id); else if (models.length === 1) setModelId(models[0].id);
```

  (assign the filtered list to `const agents = …` and pass it to `setData`);
- add

```tsx
  function chooseAgent(id: string) {
    setAgentId(id);
    const agentModel = data?.models.find(model => model.id === data.agents.find(agent => agent.id === id)?.model_id);
    if (agentModel) setModelId(agentModel.id);
  }
```

  and use `chooseAgent` instead of `setAgentId` in the **Agent** select;
- the no-agents hint becomes `Create one in Agents → New agent with Agent type Claude Code.`; the model hint becomes
  `Defaults to the agent’s model. Claude connections only: Bedrock inference profiles (eu.anthropic.claude-…) or the Anthropic API, from LLM Settings.`

`services/admin-ui/app/components/DocumentationWorkflowView.tsx`: render
`<ClaudeCodeSessionForm initialAgentId={params.get('agent') ?? ''} onCreated={id => navigate(`/documentation/sessions/${id}`)} />`.

`services/admin-ui/app/lib/claudeCode.ts`: `SessionInput.model_id?: string`; add `claude_code_tools?: string[]; agent_version?: { id: string; version: number; label: string } | null` to `SessionDetail.prompt_snapshot`.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd services/admin-ui && pnpm exec tsc --noEmit && pnpm exec vitest run app/components/__tests__/claudeCodeSessionForm.test.tsx app/components/__tests__/claudeCodeSession.test.tsx app/components/__tests__/claudeCodeSessions.test.tsx app/components/__tests__/agentTypes.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-ui/app/components/ClaudeCodeSessionForm.tsx services/admin-ui/app/components/DocumentationWorkflowView.tsx \
  services/admin-ui/app/lib/claudeCode.ts services/admin-ui/app/components/__tests__/claudeCodeSessionForm.test.tsx
git commit -m "feat(admin-ui): a Claude Code agent's Test tab starts a session with the agent and its model

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Docs and full verification

**Files:**
- Modify: `docs/user-guide.md`
- Modify: `docs/agent-profiles.md`
- Modify: `docs/agents.md`

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write the docs**

`docs/user-guide.md`:
- **§4.11 Agents:** `/agents` lists agents with **Agent type**, capabilities, model, active system prompt and status.
  **New agent** opens the wizard directly (remove the **Generic agent / Claude Code** tab sentence); the review
  **General** tab starts with **Agent type** (Native / Claude Code). In the tab table, **General** adds "Agent type
  (Native or Claude Code; switching keeps every setting), the model filtered by type, and the **Claude Code tools**
  card (Read, Grep, Glob, Bash switches) for Claude Code agents"; **Test** becomes "Native: chat with the agent.
  Claude Code: **Start a session** opens New documentation → With an agent with the agent and its model".
  Replace the **Claude Code agents** paragraph with: Claude Code agents are configured exactly like native agents
  (same tabs, prompt versions, versions). They need a Claude model (`Choose a Claude model for Claude Code agents`).
  Their sessions use the active prompt, instructions and enabled skills as the system prompt, the code projects as
  extra read-only working folders and the Claude Code tools switches. Documents, code search and code graph, SSH, MCP
  and services are saved but not given to Claude Code yet (the tabs say so). Claude Code agents never run in native
  test chats or on the multi-agent board. Agents created before this release got the built-in documentation prompt
  as prompt version 1.
- **§4.13 With an agent:** **Agent** lists enabled Claude Code agents (opened from an agent's Test tab, it is
  preselected); **Model** defaults to the agent's model; what the agent gets: its own prompt, instructions and skills
  as system prompt, then the documentation prompt with comments and context as the first message; working folders:
  this run's code projects and folders first (the first one is the working directory), then the agent's code projects.
- **§5 H:** step 1 becomes **Agents → New agent**, **Agent type: Claude Code**, choose a Claude model and write the
  system prompt → **Create agent**; step 2 may start from the agent's **Test** tab → **Start a session**.
- **Appendix A:** Claude Code agents use the Read, Grep, Glob and Bash tools switched on in their **Claude Code tools**
  card (all on by default; off tools are also disallowed); catalog tools come in a later release.

`docs/agent-profiles.md`: in **New agent → Generic agent** (rename the heading to **New agent**), describe the
**Agent type** choice and that the generation schema does not include it; `POST /api/agents/wizard` accepts
`runtime` and `claude_code_tools`. In **Persistence and APIs**, add: migration `0072_knowledge_agents_config` lifts the
0070 registration rule, adds `claude_code_tools` (default all four, values restricted) and
`claude_code_agent_is_not_the_coordinator`, restores the required prompt for every agent and gives existing Claude
Code agents the built-in documentation prompt; its down migration refuses while a Claude Code agent has more than a
name and description. `PUT /api/agents/{id}` accepts `runtime` and `claude_code_tools`; `PUT /api/agents/{id}/model`
applies the type's model rule; `GET /api/agents/{id}/config` returns `runtime` and `claude_code_tools` for both types
(`knowledge_admin.knowledge_agents.config.get_agent_config`).

`docs/agents.md`: under the Test conversations paragraph, add that test chats, live runs and the board refuse Claude
Code agents with `Claude Code agents run in Claude Code sessions. Open the agent’s Test tab to start one.`; under
**Configuration versions**, add that versions store `claude_code_tools` while the agent type always comes from the
agent.

- [ ] **Step 2: Run every suite**

Run:
```bash
cd services/admin-api && uv run --frozen pytest -q
cd ../admin-ui && pnpm exec tsc --noEmit && pnpm test
cd ../.. && python3 -m unittest discover -s tests && bash db/tests/run.sh
```
Expected: everything passes. For any failure, check out `main` in a separate worktree and run the same test. Accept it
only if it also fails there, and record it in the PR description. Never run make targets.

- [ ] **Step 3: Check for leftovers**

Run: `git grep -n "CLAUDE_CODE_ONLY\|claude-code/agents\|ClaudeCodeAgentForm\|Claude Code SDK\|registration only\|registrations only" -- services docs/user-guide.md docs/agent-profiles.md docs/agents.md`
Expected: no matches (`db/migrations/0070_*` and `test_claude_code_migration.py` keep the 0070 wording and are not
searched).

Run: `git grep -n "get_agent_config" -- services/admin-api/src`
Expected: the definition only in `knowledge_agents/config.py`; `agents.py` re-exports it.

Run: `git grep -n "require_native" -- services/admin-api/src`
Expected: `knowledge_agents/config.py`, `agent_sessions.py` (twice) and `board.py`.

- [ ] **Step 4: Commit**

```bash
git add docs/user-guide.md docs/agent-profiles.md docs/agents.md
git commit -m "docs: one agent config for Native and Claude Code agents (knowledge agents step 1)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

## Follow-ups (out of scope for this plan)

- **Step 2 — tool catalog and gateway:** `knowledge_agents/catalog.py`, the `agent-tool-gateway` service, session tool
  tokens and the in-process `knowledge` MCP server; Claude Code agents then use documents, code graph, SSH, MCP and
  services, and the catalog tool notes added in Task 6 go away.
- **Step 3 — native agents through the session API:** `agent-session-worker`, `/api/agent-sessions*`, and one Test
  tab on sessions for both types (replacing the Task 6 link and re-enabling **Test vN** for Claude Code agents).
- **Step 4 — documentation generation calls an agent:** the unified New documentation flow and removal of the special
  documentation runtimes.
