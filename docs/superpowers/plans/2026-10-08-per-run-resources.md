# Per-run Resources (Role Agents) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An agent either works with **fixed** resources, as today, or is a **role agent** whose resources are passed
**per run**: a Code analyst that is asked about `payments-api` in one run and `helpers` in the next. The user's first
target: "create a code analysis agent that receives the code path as a parameter", testable on its own from its
**Test** tab with a chosen code project, and usable from **Docs → Generate documents**. A new **Resources: Fixed / Per
run** setting (General tab, New agent wizard page 1, All agents column) is versioned like the rest of the
configuration. A per-run agent's saved selections become what a caller **may** pass; each run carries **run resources**
(`code_project_ids`, `collection_ids`, `server_ids`, `service_ids`) that are validated against those selections and
recorded with the run. The run's effective configuration narrows each resource list to exactly the run's resources, so
the existing tools, which only reach what the configuration lists, can never reach anything else, and a kind with
nothing passed is refused ("No code project was given for this run"), never widened to "all". Generate documents gets a
**Documents** choice (RAG collections); per-run agents receive all chosen sources as run resources, fixed agents keep
their own configuration and get the sources in the first message only.

**Architecture:**
- **Schema:**
  - **0075 `agent_resources`:** `ka.agents.resources text NOT NULL DEFAULT 'fixed'` (`fixed` | `per_run`) and the check
    `per_run_agent_is_not_the_coordinator`. The down migration refuses while per-run agents or per-run configuration
    versions exist (0074 would run them with their whole selection).
  - **0076 `run_resources`:** nullable `run_resources jsonb` (an object) on `ka.agent_sessions_cc` (Generate documents
    sessions), `ka.agent_sessions` (test chats) and `ka.operations_runs` (test chat turns). NULL for fixed agents.
- **Config (`knowledge_agents/config.py`):** the snapshot carries `resources` (versions saved before 0075 read as
  `fixed`; the active version's mode is the one that runs). `per_run(config)` and `require_run_resources(config)`:
  a per-run configuration without `run_resources` never runs (`agents.create_agent`, test chats, Generate documents,
  the board, `knowledge_llm.profiles.profile(runnable=True)`).
- **Run resources (`knowledge_agents/run_resources.py`, new):** `RunResources` (the request model),
  `resolve(conn, project_id, config, requested, *, environment_ids=())` (validate, expand services and environments,
  narrow, record), `options(conn, project_id, config)` (what a caller may pass, per kind) and `RunResourceError`.
- **Tools:** `tools.require_given(config, kind)` refuses a kind the run got nothing of; `files._roots`,
  `documents._allowed`, `code_search`, `ast_grep`, `code_graph` and `ssh_run` call it; `documents._allowed` never
  returns "all" for a per-run configuration. `agents.get_prompt` says the run was given only the listed resources.
- **Callers:** test chats (`agent_sessions.py`: `RunInput.resources`, `SessionInput.resources`, pinned narrowed
  config, recorded on the session and each turn; follow-ups cannot change them); `GET /api/agents/{id}/run-resources`
  (picker options); Generate documents (`claude_code_api.py`, `generate_documents.py`: `collection_ids`, a
  **Documents** section in the first message, per-run agents resolved with the form's sources and environments).
- **admin-ui:** General **Resources** select (coordinator: Fixed, no choice), wizard page 1 (**Agent type** and
  **Resources** first, not repeated on the review), All agents **Resources** column (`Fixed` / `Per run · code (any)`),
  per-run notes on the capability tabs, the Test tab **Resources for this run** picker (shown in **Show config**), and
  the Generate documents **Documents** choice with per-run filtering and notes.

**Tech Stack:**
- PostgreSQL 17 with pgvector, golang-migrate SQL migrations, `db/tests/run.sh`;
- FastAPI, pydantic v2, psycopg3, OpenAI Agents SDK 0.19.2 function tools (`services/admin-api`), pytest against a
  disposable PostgreSQL container (`knowledge-agent-v3-postgres:17.10-vector0.8.6`);
- React Router 7 with shadcn/ui, Vitest with Testing Library (`services/admin-ui`).

**Spec:** `docs/superpowers/specs/2026-10-08-per-run-resources-design.md` (binding), with
`docs/superpowers/specs/2026-10-07-knowledge-agents-design.md` for context. Additions decided after the spec (binding):
the Generate documents **Documents** choice; per-run agents receive all chosen sources as run resources while fixed
agents keep their own configuration and get the sources in the first message only; Claude Code agents use run code
projects as working folders and say that other kinds come with the tool gateway; the Test tab picker applies to native
test chats and is fixed for the conversation; **Task 1 ships the setting alone** (user decision), with Agent type and
Resources as the first two fields of wizard page 1 and not repeated on its review. Where the plan differs from the
spec's letter, see **Decisions**.

## Global Constraints

- Implementers and reviewers never run make targets and never touch .env or .local. Never touch the running Docker
  stack's database: tests use the disposable PostgreSQL fixture (`tests/conftest.py`) and `db/tests/run.sh`.
- Credentials never logged, stored in events or history unredacted, or passed to Bash. Run resources are ids only; a
  narrowed configuration keeps credential references only (`private_key_ref`, `api_key_ref`, `bearer_token_ref`);
  `agents.load_credentials` decrypts only what the narrowed configuration lists, so a server a run was not given has
  its key left encrypted.
- Commit trailer: `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Python stays compatible with 3.12. TypeScript passes `pnpm exec tsc --noEmit`.
- Migrations: `0075_agent_resources` (Task 1, `db/tests/run.sh` rollback pin `73` → `74`) and `0076_run_resources`
  (Task 2, pin `74` → `75`), each with `.up.sql` and `.down.sql`, each wrapped in `BEGIN; … COMMIT;` with
  `SET search_path = ka, public;`.
- Run resources shape (stored and pinned, ids as strings, each once, in the caller's order followed by what services and
  environments bring): `{"code_project_ids": [...], "collection_ids": [...], "server_ids": [...], "service_ids": [...]}`
  — all four keys, always, for per-run runs; `NULL` (column) / absent (config key) for fixed agents.
- Texts (API):
  - `config.NO_RUN_RESOURCES = 'This agent receives its resources per run, and this run was started without them.'`
    (409 for test chats as their other configuration errors, 400 for Generate documents, a `ValueError` elsewhere);
  - `profiles_api.COORDINATOR_FIXED = 'The coordinator uses fixed resources.'` (400);
  - board: `'Per-run agents cannot join the multi-agent board yet.'` (400);
  - `run_resources.FIXED = 'This agent uses fixed resources.'` (400);
  - `run_resources.NOT_USED = 'This agent does not use {what}.'` with `code projects`, `document collections`,
    `servers`, `services` (400);
  - `run_resources.NOT_ALLOWED = '{label} {name} is not allowed for this agent.'` with `Code project`, `Collection`,
    `Server`, `Service` (400);
  - `run_resources.NOT_AVAILABLE = '{label} {name} is not available: it was removed, archived or disabled.'` (400; the
    id when the resource does not exist in this workspace);
  - `agent_sessions.KEEPS_RESOURCES = 'A conversation keeps the resources it started with. Start a new session to change them.'` (409);
  - `claude_code_api.NO_RUN_FOLDER = 'Claude Code needs at least one working folder: choose code projects, or environments or services with code projects, that this agent accepts.'` (400);
  - `claude_code_api.NOTHING_CHOSEN = 'Choose at least one code project, collection, environment, service or server to document.'`.
- Texts (tool errors the model sees, `tools.NOTHING_GIVEN`): `code` → `No code project was given for this run.`,
  `documents` → `No document collection was given for this run.`, `ssh` → `No server was given for this run.`,
  `files` → `No code project or document collection was given for this run.`.
- Texts (prompt, per-run only): `This run was given only the resources listed below. Ask for anything else instead of looking for it.`
- UI texts: `RESOURCE_MODES = [['fixed','Fixed'],['per_run','Per run']]`; help `Always works with the resources selected
  in its tabs.` / `Each run says what to work on: callers pass code projects, collections, servers or services from
  those selected in its tabs.`; coordinator `The coordinator works through the specialists it delegates to.`; wizard
  review line `<Native|Claude Code> · <Fixed|Per run>` followed by `Change them on page 1 with Edit role.`; list
  `Fixed`, `Per run · code (any) · servers (2)`, `Per run · nothing`; capability notes and Generate documents notes as
  written in Tasks 7 and 8; Test tab `Resources for this run`.

## Review Focus

1. **A per-run run reaches a resource it was not given, or everything.** Examples: a list left un-narrowed
   (`code.graphs`, legacy `documents.folders`), `documents.all_collections` left `true` so `search_documents` searches
   every collection, a tool that treats an empty list as "all", a per-run configuration without `run_resources`
   reaching `create_agent` (board, live chats, legacy profile loader), keys decrypted for servers the run was not given.
   Pinned in:
   - Task 1: `test_a_per_run_agent_never_runs_with_its_whole_selection`;
   - Task 2: `test_a_run_gets_exactly_its_resources_and_nothing_means_nothing`,
     `test_credentials_are_decrypted_only_for_the_servers_a_run_was_given`;
   - Task 3: `test_file_and_code_tools_reach_only_the_given_code_projects`,
     `test_a_kind_the_run_got_nothing_of_is_refused_never_widened`, `test_document_search_uses_only_the_given_collections`,
     `test_ssh_reaches_only_the_given_servers`;
   - Task 4: `test_a_test_chat_runs_with_exactly_the_resources_it_started_with`;
   - Task 5: `test_a_per_run_native_agent_gets_exactly_the_forms_sources`.
2. **A caller passes something outside the agent's selection.** Examples: a code project the agent did not select, an
   id from another workspace or that does not exist, an archived project, a server without SSH, a kind the agent has
   switched off, a code project or server a service or environment brings that the agent does not allow, resources
   passed to a fixed agent, a follow-up message that swaps the resources. Pinned in:
   - Task 2: `test_resources_outside_the_selection_the_workspace_or_the_agents_kinds_are_refused`,
     `test_services_and_environments_bring_their_code_projects_and_servers`;
   - Task 4: `test_test_chats_refuse_resources_a_per_run_agent_may_not_get_and_fixed_agents_any`,
     `test_a_test_chat_runs_with_exactly_the_resources_it_started_with` (409 on a changed follow-up);
   - Task 5: `test_generate_documents_refuses_sources_a_per_run_agent_may_not_get`.
3. **The setting and its versions disagree, or a rollback widens access.** Examples: a version saved before 0075 not
   read as fixed; the list showing the working copy instead of the active version; the coordinator set to per run;
   0075 rolled back with per-run agents (they would run with their whole selection); a draft created from a per-run
   conversation's suggestion saving one run's narrowed lists as the agent's configuration. Pinned in:
   - Task 1: `test_agents_are_fixed_by_default_and_the_coordinator_stays_fixed`,
     `test_down_refuses_per_run_agents_and_versions_then_up_makes_every_agent_fixed`,
     `test_the_setting_is_saved_versioned_listed_and_refused_for_the_coordinator`;
   - Task 4: `test_a_suggestion_from_a_per_run_chat_drafts_the_whole_configuration`.
4. **Fixed agents change beyond the decided Generate documents change.** Examples: a fixed Claude Code agent losing the
   chosen or linked code projects as working folders; a fixed agent refused because the form has sources; the first
   message differing between fixed and per-run agents; a per-run Claude Code agent getting the agent's whole selection
   as working folders. Pinned in:
   - Task 5: `test_fixed_agents_keep_their_configuration_and_get_the_sources_in_the_first_message`,
     `test_a_per_run_claude_code_agent_works_in_exactly_the_run_code_projects`, the updated
     `test_native_agents_start_sessions_with_their_own_config` and the unchanged Claude Code session tests.
5. **The screens offer or show something else than what runs.** Examples: the wizard still showing a second Agent
   type selector on its review; the Test tab picker offering a resource the agent does not allow, sending resources
   with a follow-up, or Show config without the choice; the Generate form offering a kind a per-run agent does not
   take; the list column ignoring the active version. Pinned in:
   - Task 1: wizard `step 1 starts with Agent type and Resources, which shape the proposal and a blank start` and
     `Agent type and Resources come from page 1; switching to Claude Code there clears a non-Claude model`;
   - Task 6: `a per-run agent picks the resources for the run and shows them in the configuration`,
     `the chosen resources start the conversation once and stay with it`;
   - Task 7: `a per-run agent is offered only what it accepts and receives exactly the choice`;
   - Task 8: `test_the_list_says_what_a_per_run_agent_accepts`.

## Decisions (where the spec leaves a choice)

- **Task 1 ships the setting alone, and safely.** A per-run agent must never run with its whole selection, so Task 1
  also adds `require_run_resources`: until a run passes resources (Tasks 4 and 5), a per-run agent does not run at
  all (test chats 409, Generate documents 400, the board 400, `create_agent` `ValueError`). Tasks 4 and 5 lift this
  by passing run resources; the guard stays for everything else (board, live runs started without resources go
  through `resolve`, which always records `run_resources`).
- **The active version decides the mode,** like every other setting: `get_agent_config` reads `resources` from the
  active version (missing → `fixed`), the All agents list shows that, and the General tab edits the working copy (the
  existing "These tabs edit the working configuration" notice applies). A test with **Test vN** uses that version's
  mode.
- **"Allowed" is the snapshot's selection.** Code projects and collections with **All** allow any usable one in the
  workspace (loaded fresh in the snapshot's shape); otherwise the snapshot lists exactly what may be passed. SSH
  servers keep their per-server instructions, skills, MCP and connection overrides because they are taken from the
  snapshot.
- **Narrowing happens when the run starts and is pinned,** not per turn in the worker: test chats pin
  `ka.agent_sessions.config` and native Generate documents sessions pin `agent_config` already, so pinning the narrowed
  configuration gives every later turn exactly the run's resources without touching `native_sessions.py`'s worker.
- **Kinds stay switched on with empty lists** when nothing was passed, so their tools answer "No … was given for this
  run". `ssh_run` is not registered when the run has no server (as today for an agent without usable servers); it
  still refuses if called.
- **Services and environments expand.** A chosen service brings its (non-archived) code projects and the servers it is
  deployed on that allow SSH; a Generate documents environment brings its services and servers (and through the
  services their code projects and servers). What they bring is added only for kinds the agent uses; unusable ones
  (archived, no SSH) are skipped; usable ones must be allowed, or the run is refused naming them. Environments are not
  a run resource kind of their own.
- **Fixed agents in Generate documents keep their own configuration.** Native fixed agents no longer get the form's
  code projects as code roots (`native_sessions.code_roots` is removed): they read what their Code projects tab gives
  them, and the sources are in the first message. Fixed Claude Code agents keep today's working folders (chosen and
  linked code projects, then their own), because Claude Code has no other tool to read them yet. Per-run agents get
  exactly the form's sources; per-run Claude Code agents get the run's code projects as working folders, and the other
  kinds are recorded and described in the first message until the tool gateway (knowledge agents step 2).
- **Follow-ups never pass resources** (409 `KEEPS_RESOURCES`), even the same ones: the conversation keeps what it
  started with, like its configuration.
- **A suggestion from a per-run test chat drafts the whole configuration:** `agent_versions.source` loads the version
  the conversation used instead of the conversation's narrowed configuration.
- **The board and the legacy profile loader refuse per-run agents** (`Per-run agents cannot join the multi-agent board
  yet.`; `profile(runnable=True)` raises `ModelUnavailable`). Agents calling agents and passing resources come with
  the builder (out of scope).
- **0075 down refuses per-run agents and versions;** 0076 down only drops the records (pinned configurations keep
  their narrowed lists and `run_resources`, so nothing widens).

## Delivery and test boundaries

Tasks land in order on `feature/per-run-resources`, and every suite is green after each task:
- Task 1 (the setting) ships alone. Task 2 (migration 0076 and `run_resources.py`) needs Task 1. Task 3 (tools) needs
  Task 2's shape only (`run_resources` key). Task 4 (test chats) needs Tasks 2 and 3. Task 5 (Generate documents)
  needs Tasks 2 and 3. Task 6 (Test tab UI) needs Task 4. Task 7 (Generate form UI) needs Tasks 4 (options endpoint),
  5 and 6 (the shared UI types). Task 8 (list text and tab notes) needs Task 1. Task 9 updates the docs and runs every suite.

---

### Task 1: The Resources setting — Fixed / Per run, saved, versioned, on General, the wizard and the list

**Files:**
- Create: `db/migrations/0075_agent_resources.up.sql`, `db/migrations/0075_agent_resources.down.sql`
- Modify: `db/tests/run.sh` (rollback pin `73` → `74`)
- Modify: `services/admin-api/src/knowledge_admin/knowledge_agents/config.py`
- Modify: `services/admin-api/src/knowledge_admin/profiles_api.py`
- Modify: `services/admin-api/src/knowledge_admin/agent_wizard.py`
- Modify: `services/admin-api/src/knowledge_admin/agents.py` (`create_agent`)
- Modify: `services/admin-api/src/knowledge_admin/agent_sessions.py` (`create_session`, `start_turn`)
- Modify: `services/admin-api/src/knowledge_admin/claude_code_api.py` (`create_session`)
- Modify: `services/admin-api/src/knowledge_admin/board.py` (task creation)
- Modify: `packages/llm-settings/src/knowledge_llm/profiles.py` (`profile`)
- Create: `services/admin-api/tests/test_agent_resources.py`
- Modify: `services/admin-ui/app/lib/profiles.ts`, `services/admin-ui/app/lib/agentWizard.ts`
- Modify: `services/admin-ui/app/components/AgentProfileSettings.tsx`, `AgentWizard.tsx`, `AgentsView.tsx`
- Modify: `services/admin-ui/app/components/__tests__/agentTypes.test.tsx`, `agentWizard.test.tsx`

**Interfaces:**
- Produces: `ka.agents.resources`; `config.Resources = Literal['fixed', 'per_run']`; `AgentConfig.resources`,
  `AgentConfig.run_resources: NotRequired[dict[str, list[str]]]`; `config.per_run(config) -> bool`;
  `config.require_run_resources(config) -> None`; `config.NO_RUN_RESOURCES`; `profiles_api.COORDINATOR_FIXED`;
  `ProfileInput.resources: Literal['fixed','per_run'] | None` (None keeps it); `CreateInput.resources` (default
  `fixed`); `GET /api/agents` rows and `GET /api/agents/{id}` carry `resources` (the list: the active version's).
- UI: `AgentResources`, `RESOURCE_MODES`, `RESOURCES_HELP`, `resourcesLabel(mode?)` in `lib/profiles.ts`;
  `AgentProfile.resources?`; `NewAgent.resources`.

- [ ] **Step 1: Write the failing tests**

Create `services/admin-api/tests/test_agent_resources.py`:

```python
"""Resources (0075): an agent works with fixed resources or receives them per run. The setting is saved, versioned
and refused for the coordinator; a per-run agent never runs with its whole selection."""
import asyncio
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from knowledge_llm import ModelUnavailable
from knowledge_llm.profiles import profile

from knowledge_admin.agents import create_agent
from knowledge_admin.knowledge_agents.config import NO_RUN_RESOURCES, get_agent_config, require_run_resources
from knowledge_admin.profiles_api import COORDINATOR_FIXED
from test_agents import agent_mounts, saved_agent  # noqa: F401  (fixtures)
from test_claude_code_sessions import refused, sessions  # noqa: F401  (fixture)
from test_profiles import update, wizard_payload
from test_repositories import HEADERS

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0075_agent_resources.up.sql', '0075_agent_resources.down.sql'
EMPTY_CHANGES = {'system_prompt': None, 'instructions': None, 'server_instructions': []}


def run(conn, name: str):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def has_column(conn) -> bool:
    return conn.execute("""SELECT 1 FROM information_schema.columns WHERE table_schema='ka' AND table_name='agents'
        AND column_name='resources'""").fetchone() is not None


def listed(client) -> dict:
    return {row['id']: row for row in client.get('/api/agents', headers=HEADERS).json()['agents']}


def new_version(client, label: str) -> dict:
    source = client.get('/api/agents/operations/versions/source', headers=HEADERS).json()
    created = client.post('/api/agents/operations/versions', headers=HEADERS, json={
        'label': label, 'source_hash': source['source_hash'], 'changes': EMPTY_CHANGES})
    assert created.status_code == 201, created.text
    return {**created.json(), 'source': source}


def test_agents_are_fixed_by_default_and_the_coordinator_stays_fixed(store):
    with store.connect() as conn:
        assert {row['resources'] for row in conn.execute('SELECT resources FROM ka.agents')} == {'fixed'}
        conn.execute("UPDATE ka.agents SET resources='per_run' WHERE id='search'")
        for statement, error in (
                ("UPDATE ka.agents SET resources='per_run' WHERE is_coordinator", psycopg.errors.CheckViolation),
                ("UPDATE ka.agents SET resources='sometimes' WHERE id='code'", psycopg.errors.CheckViolation),
                ("UPDATE ka.agents SET resources=NULL WHERE id='code'", psycopg.errors.NotNullViolation)):
            with pytest.raises(error), conn.transaction():
                conn.execute(statement)


def test_down_refuses_per_run_agents_and_versions_then_up_makes_every_agent_fixed(store, saved_agent):
    client = saved_agent[0]
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET resources='per_run' WHERE id='operations'")
    assert new_version(client, 'Per run')['source']['config']['resources'] == 'per_run'
    with store.connect() as conn:
        with pytest.raises(psycopg.errors.RaiseException, match='Per-run agents'):
            run(conn, DOWN)
        conn.rollback()
        conn.execute("UPDATE ka.agents SET resources='fixed' WHERE id='operations'")
        conn.commit()
        with pytest.raises(psycopg.errors.RaiseException, match='Per-run agents'):  # its versions are still per run
            run(conn, DOWN)
        conn.rollback()
        conn.execute("UPDATE ka.agents SET active_config_version_id=NULL WHERE id='operations'")
        conn.execute("DELETE FROM ka.agent_versions WHERE agent='operations'")
        conn.commit()
        run(conn, DOWN)
        conn.commit()
        assert not has_column(conn)
        run(conn, UP)
        conn.commit()
        assert has_column(conn)
        assert conn.execute("SELECT resources FROM ka.agents WHERE id='operations'").fetchone()['resources'] == 'fixed'


def test_the_setting_is_saved_versioned_listed_and_refused_for_the_coordinator(store, saved_agent):
    client = saved_agent[0]
    operations = client.get('/api/agents/operations', headers=HEADERS).json()
    assert operations['resources'] == 'fixed'
    saved = update(client, operations, resources='per_run')
    assert saved.status_code == 200, saved.text
    assert saved.json()['resources'] == 'per_run'
    assert update(client, saved.json(), resources=None).json()['resources'] == 'per_run'  # omitted: kept
    assert get_agent_config('operations', conninfo=store.conninfo)['resources'] == 'per_run'
    coordinator = client.get('/api/agents/coordinator', headers=HEADERS).json()
    assert coordinator['resources'] == 'fixed'
    refusal = update(client, coordinator, resources='per_run')
    assert refusal.status_code == 400 and refusal.json()['error']['message'] == COORDINATOR_FIXED
    assert (listed(client)['operations']['resources'], listed(client)['coordinator']['resources']) == ('per_run', 'fixed')
    # The first draft also activates "Initial configuration" (per run). A version keeps its mode; the list shows what
    # runs (the active version), General the working copy.
    version = new_version(client, 'Per run analyst')
    assert get_agent_config('operations', conninfo=store.conninfo, version_id=version['id'])['resources'] == 'per_run'
    back = update(client, client.get('/api/agents/operations', headers=HEADERS).json(), resources='fixed')
    assert back.json()['resources'] == 'fixed'
    assert listed(client)['operations']['resources'] == 'per_run'
    with store.connect() as conn:  # versions saved before 0075 have no mode: they are fixed
        conn.execute("UPDATE ka.agent_versions SET config=config-'resources' WHERE agent='operations'")
    assert get_agent_config('operations', conninfo=store.conninfo)['resources'] == 'fixed'
    assert listed(client)['operations']['resources'] == 'fixed'
    created = client.post('/api/agents/wizard', headers=HEADERS, json=wizard_payload('Code analyst', resources='per_run'))
    assert created.status_code == 201, created.text
    assert client.get(f"/api/agents/{created.json()['id']}", headers=HEADERS).json()['resources'] == 'per_run'
    assert client.post('/api/agents/wizard', headers=HEADERS,
                       json=wizard_payload('Odd agent', resources='sometimes')).status_code == 400


def test_a_per_run_agent_never_runs_with_its_whole_selection(store, saved_agent):
    client = saved_agent[0]
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET resources='per_run' WHERE id='operations'")
    config = get_agent_config('operations', conninfo=store.conninfo)
    with pytest.raises(ValueError, match='per run'):
        require_run_resources(config)
    require_run_resources({**config, 'run_resources': {}})  # a run that passed its resources
    require_run_resources({**config, 'resources': 'fixed'})

    async def build():
        async with create_agent(config, credentials={}, timeout_seconds=5):
            pass
    with pytest.raises(ValueError, match='per run'):
        asyncio.run(build())
    board = client.post('/api/agent-board/tasks', headers=HEADERS,
                        json={'id': str(uuid4()), 'task': 'Check the queue', 'subagent_ids': ['operations']})
    assert board.status_code == 400 and board.json()['error']['message'] == 'Per-run agents cannot join the multi-agent board yet.'
    with store.connect() as conn:
        with pytest.raises(ModelUnavailable):
            profile(conn, 'operations', runnable=True)


def test_test_chats_refuse_per_run_agents_until_runs_pass_resources(store, saved_agent):
    """Replaced in Task 4, when test chats pass run resources."""
    client = saved_agent[0]
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET resources='per_run' WHERE id='operations'")
    chat = client.post('/api/agents/operations/sessions', headers=HEADERS, json={'title': 'Test', 'is_test': True})
    assert chat.status_code == 409 and chat.json()['error']['message'] == NO_RUN_RESOURCES
    run_ = client.post('/api/agents/operations/runs', headers=HEADERS, json={'task': 'Check the queue', 'is_test': True})
    assert run_.status_code == 409 and NO_RUN_RESOURCES in run_.text
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_sessions').fetchone()['n'] == 0


def test_generate_documents_refuses_per_run_agents_until_runs_pass_resources(sessions):
    """Replaced in Task 5, when Generate documents passes run resources."""
    with sessions.store.connect() as conn:
        conn.execute("UPDATE ka.agents SET resources='per_run' WHERE id=%s", (sessions.agent,))
    assert refused(sessions) == NO_RUN_RESOURCES
```

In `services/admin-ui/app/components/__tests__/agentTypes.test.tsx`:
- after the other `vi.mock` lines add `vi.mock('../CoordinatorSettings', () => ({ default: () => null }));`;
- append:

```tsx
test('General sets Resources next to the agent type', async () => {
  const user = userEvent.setup();
  open('/agents/search');
  const resources = await screen.findByRole('combobox', { name: 'Resources' });
  expect(resources).toHaveTextContent('Fixed');
  expect(screen.getByText('Always works with the resources selected in its tabs.')).toBeVisible();
  await user.click(resources);
  await user.click(screen.getByRole('option', { name: 'Per run' }));
  expect(screen.getByText(/^Each run says what to work on/)).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Save details' }));
  await waitFor(() => expect(profiles.saveAgent).toHaveBeenCalledWith(expect.objectContaining({ id: 'search', resources: 'per_run' })));
});

test('the coordinator shows Fixed resources without a choice', async () => {
  vi.mocked(profiles.getAgent).mockResolvedValue({ ...structuredClone(writer), id: 'coordinator', name: 'Coordinator',
    runtime: 'openai', is_coordinator: true, resources: 'fixed' });
  open('/agents/coordinator');
  expect(await screen.findByText('The coordinator works through the specialists it delegates to.')).toBeVisible();
  expect(screen.queryByRole('combobox', { name: 'Resources' })).toBeNull();
});

test('All agents shows how each agent gets its resources', async () => {
  vi.mocked(profiles.getAgents).mockResolvedValue({ agents: [
    { ...writer, id: 'search', name: 'Search agent', runtime: 'openai', resources: 'fixed', model_name: 'GPT', prompt_title: 'Search', prompt_version: 2 },
    { ...writer, resources: 'per_run', model_name: 'Claude on Bedrock', prompt_title: 'Built-in documentation writer', prompt_version: 1 }] });
  render(<MemoryRouter initialEntries={['/agents']}><Routes><Route path="/agents" element={<AgentsView />} /></Routes></MemoryRouter>);
  const row = (await screen.findByRole('link', { name: 'Technical writer' })).closest('tr')!;
  expect(screen.getByRole('columnheader', { name: 'Resources' })).toBeVisible();
  expect(within(row).getByText('Per run')).toBeVisible();
  expect(within(screen.getByRole('link', { name: 'Search agent' }).closest('tr')!).getByText('Fixed')).toBeVisible();
});
```

In `services/admin-ui/app/components/__tests__/agentWizard.test.tsx`:
- in the first test, the `createWizardAgent` expectation gets `resources: 'fixed'` next to `runtime: 'openai'`;
- replace the test `the Agent type choice filters models and is sent with the agent` with:

```tsx
test('Agent type and Resources come from page 1; switching to Claude Code there clears a non-Claude model', async () => {
  vi.mocked(api.getWizardOptions).mockResolvedValue({ ...structuredClone(options), catalog: { ...structuredClone(options.catalog), models: [
    { id: 'model', name: 'Claude', model: 'eu.anthropic.claude-test', provider: 'bedrock', api_format: 'native' },
    { id: 'gpt', name: 'GPT', model: 'gpt-5', provider: 'openai', api_format: 'responses' }] } });
  const user = userEvent.setup();
  render(<MemoryRouter><AgentWizard onClose={vi.fn()} onCreated={vi.fn()} /><Toaster /></MemoryRouter>);
  await screen.findByLabelText('What should this agent do?');
  await user.click(screen.getByRole('button', { name: 'Start blank' }));
  expect(screen.queryByRole('combobox', { name: 'Agent type' })).toBeNull();  // not repeated on the review
  expect(screen.queryByRole('combobox', { name: 'Resources' })).toBeNull();
  expect(screen.getByLabelText('Agent type and resources')).toHaveTextContent('Native · Fixed');
  await user.click(screen.getByRole('combobox', { name: 'Agent model' }));
  await user.click(screen.getByRole('option', { name: /^GPT/ }));
  await user.click(screen.getByRole('button', { name: 'Edit role' }));
  await user.click(screen.getByRole('combobox', { name: 'Agent type' }));
  await user.click(screen.getByRole('option', { name: 'Claude Code' }));
  await user.click(screen.getByRole('combobox', { name: 'Resources' }));
  await user.click(screen.getByRole('option', { name: 'Per run' }));
  await user.click(screen.getByRole('button', { name: 'Back to proposal' }));
  expect(screen.getByLabelText('Agent type and resources')).toHaveTextContent('Claude Code · Per run');
  expect(screen.getByRole('combobox', { name: 'Agent model' })).toHaveTextContent('No model (choose later)');  // GPT does not fit
  await user.click(screen.getByRole('combobox', { name: 'Agent model' }));
  expect(screen.queryByRole('option', { name: /^GPT/ })).toBeNull();
  await user.click(screen.getByRole('option', { name: /^Claude/ }));
  await user.type(screen.getByLabelText('Agent name'), 'Writer');
  await user.click(screen.getByRole('tab', { name: 'Prompt' }));
  await user.type(screen.getByLabelText('System prompt'), 'Write documentation.');
  await user.click(screen.getByRole('button', { name: 'Create agent' }));
  expect(api.createWizardAgent).toHaveBeenCalledWith(expect.objectContaining({ name: 'Writer', model_id: 'model',
    runtime: 'claude_code', resources: 'per_run', claude_code_tools: ['Read', 'Grep', 'Glob', 'Bash'] }));
});
```

- replace the test `step 1 starts with the Agent type, which shapes the proposal and a blank start` with:

```tsx
test('step 1 starts with Agent type and Resources, which shape the proposal and a blank start', async () => {
  vi.mocked(api.getWizardOptions).mockResolvedValue({ ...structuredClone(options), default_model_id: 'gpt', catalog: { ...structuredClone(options.catalog), models: [
    { id: 'gpt', name: 'GPT', model: 'gpt-5', provider: 'openai', api_format: 'responses' },
    { id: 'model', name: 'Claude', model: 'eu.anthropic.claude-test', provider: 'bedrock', api_format: 'native' }] } });
  const { user } = await open();
  expect(screen.getAllByRole('combobox').map(box => box.getAttribute('id')).slice(0, 2)).toEqual(['wizard-agent-type', 'wizard-resources']);
  expect(screen.getByRole('combobox', { name: 'Agent type' })).toHaveTextContent('Native');
  expect(screen.getByRole('combobox', { name: 'Resources' })).toHaveTextContent('Fixed');
  await user.click(screen.getByRole('combobox', { name: 'Agent type' }));
  await user.click(screen.getByRole('option', { name: 'Claude Code' }));
  await user.click(screen.getByRole('button', { name: 'Generate proposal' }));
  expect(api.generateAgent).toHaveBeenCalledWith('Investigate payment incidents.', 'gpt', 'claude_code');
  expect(await screen.findByLabelText('Agent type and resources')).toHaveTextContent('Claude Code · Fixed');
  expect(screen.queryByRole('combobox', { name: 'Agent type' })).toBeNull();
});
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_agent_resources.py -q`
Expected: FAIL: `column "resources" does not exist` (and `ImportError` for `NO_RUN_RESOURCES`).

Run: `cd services/admin-ui && pnpm exec vitest run app/components/__tests__/agentTypes.test.tsx app/components/__tests__/agentWizard.test.tsx`
Expected: FAIL: no combobox named `Resources`, no `Agent type and resources` line.

- [ ] **Step 3: Implement the migration**

Create `db/migrations/0075_agent_resources.up.sql`:

```sql
BEGIN;
SET search_path = ka, public;

-- An agent works with fixed resources (its own selections, as before) or receives them per run: its selections are
-- then what a caller may pass, and each run says what to work on. The coordinator always works with fixed resources.
ALTER TABLE agents
    ADD COLUMN resources text NOT NULL DEFAULT 'fixed'
        CONSTRAINT agent_resources_known CHECK (resources IN ('fixed','per_run')),
    ADD CONSTRAINT per_run_agent_is_not_the_coordinator CHECK (resources = 'fixed' OR NOT is_coordinator);
COMMENT ON COLUMN agents.resources IS
    'fixed: the agent always uses its selections. per_run: its selections are what a run may be given; each run passes its own.';

COMMIT;
```

Create `db/migrations/0075_agent_resources.down.sql`:

```sql
BEGIN;
SET search_path = ka, public;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM agents WHERE resources = 'per_run')
       OR EXISTS (SELECT 1 FROM agent_versions WHERE config->>'resources' = 'per_run') THEN
        RAISE EXCEPTION 'Per-run agents or per-run configuration versions exist, and 0074 would run them with their whole selection; set the agents to Fixed and delete those versions (ka.agent_versions WHERE config->>''resources'' = ''per_run'') before rolling back 0075';
    END IF;
END $$;

ALTER TABLE agents
    DROP CONSTRAINT per_run_agent_is_not_the_coordinator,
    DROP COLUMN resources;

COMMIT;
```

`db/tests/run.sh`: change `[ "$rolled_back_version" = 73 ]` to `[ "$rolled_back_version" = 74 ]`.

- [ ] **Step 4: Implement the config, the guard and the APIs**

`services/admin-api/src/knowledge_admin/knowledge_agents/config.py`:
- after `ClaudeCodeTool = …` add `Resources = Literal['fixed', 'per_run']`, and after `NATIVE_ONLY = …` add
  `NO_RUN_RESOURCES = 'This agent receives its resources per run, and this run was started without them.'`;
- in `AgentConfig`, after `claude_code_tools`, add `resources: Resources`, and after `config_version` add
  `run_resources: NotRequired[dict[str, list[str]]]`;
- after `require_native` add:

```python
def per_run(config: Mapping[str, Any]) -> bool:
    """Per-run agents: the saved selections are what a run may be given (versions saved before 0075 are fixed)."""
    return config.get('resources', 'fixed') == 'per_run'


def require_run_resources(config: Mapping[str, Any]) -> None:
    """A per-run agent runs only with the resources its run was given, never with its whole selection."""
    if per_run(config) and 'run_resources' not in config:
        raise ValueError(NO_RUN_RESOURCES)
```

- in `get_agent_config`: add `resources` to the `SELECT … FROM ka.agents` column list (after `claude_code_tools`); in
  the version branch after `config.setdefault('claude_code_tools', …)` add
  `config.setdefault('resources', 'fixed')  # versions saved before 0075`; in the returned dict after
  `'claude_code_tools': list(agent['claude_code_tools']),` add `'resources': agent['resources'],`.

`services/admin-api/src/knowledge_admin/agents.py`, `create_agent`: import `require_run_resources` from
`knowledge_admin.knowledge_agents.config` (next to `get_agent_config`) and directly after
`if not config['enabled']: raise ValueError('This agent is disabled.')` add `require_run_resources(config)`.

`services/admin-api/src/knowledge_admin/agent_sessions.py`: import `require_run_resources` with `require_native`; in
`create_session` after `require_native(config)` add `require_run_resources(config)`; in `start_turn` after
`require_native(config)` add `require_run_resources(config)` (inside the same `try`, so it answers 409 before anything
is saved).

`services/admin-api/src/knowledge_admin/claude_code_api.py`, `create_session`: import `require_run_resources` from
`knowledge_admin.knowledge_agents.config` and after the `try: config = get_agent_config(…) except …` block add:

```python
            try:  # replaced by run resources in Task 5
                require_run_resources(config)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from None
```

`services/admin-api/src/knowledge_admin/board.py`, task creation: in the `profiles = conn.execute('''SELECT a.id,…`
query add the column
`CASE WHEN v.id IS NULL THEN a.resources ELSE coalesce(v.config->>'resources','fixed') END AS resources` after
`a.runtime,`, and after the `Claude Code agents cannot join the multi-agent board.` check add:

```python
            if any(p['resources'] != 'fixed' for p in profiles):
                raise HTTPException(400, 'Per-run agents cannot join the multi-agent board yet.')
```

`packages/llm-settings/src/knowledge_llm/profiles.py`, `profile`: after the runtime check add

```python
    if runnable and row.get('resources', 'fixed') != 'fixed':  # per-run agents run only with their run's resources
        raise ModelUnavailable('Agent not found or not supported by this runtime.')
```

`services/admin-api/src/knowledge_admin/profiles_api.py`:
- after `WORKING_REFUSED = …` add `COORDINATOR_FIXED = 'The coordinator uses fixed resources.'`;
- `ProfileInput`: after `claude_code_tools` add
  `resources: Literal['fixed', 'per_run'] | None = None  # None keeps the setting`;
- `listing`: inside `if config is not None:` add `row['resources'] = config.get('resources', 'fixed')  # versions saved before 0075 are fixed`;
- `update`: after `runtime = value.runtime or old['runtime']` add

```python
                resources = value.resources or old['resources']
                if old['is_coordinator'] and resources != 'fixed':
                    raise HTTPException(400, COORDINATOR_FIXED)
```

  extend `switch` with
  `**({'resources': {'from': old['resources'], 'to': resources}} if resources != old['resources'] else {})`, and the
  `UPDATE ka.agents SET …` statement with `resources=%s` after `claude_code_tools=%s` (parameter `resources` after
  `tools`).

`services/admin-api/src/knowledge_admin/agent_wizard.py`, `CreateInput`: add
`resources: Literal['fixed', 'per_run'] = 'fixed'`; in `create`, add `resources` to the `INSERT INTO ka.agents(…)`
column list after `claude_code_tools` with one more `%s` and the parameter `value.resources`, and add
`'resources': value.resources` to the `agent.created` audit details.

- [ ] **Step 5: Implement the UI**

`services/admin-ui/app/lib/profiles.ts`, after `AGENT_TYPE_HELP`:

```ts
export type AgentResources = 'fixed' | 'per_run';
export const RESOURCE_MODES: [AgentResources, string][] = [['fixed', 'Fixed'], ['per_run', 'Per run']];
export const resourcesLabel = (mode?: AgentResources) => mode === 'per_run' ? 'Per run' : 'Fixed';
export const RESOURCES_HELP: Record<AgentResources, string> = {
  fixed: 'Always works with the resources selected in its tabs.',
  per_run: 'Each run says what to work on: callers pass code projects, collections, servers or services from those selected in its tabs.',
};
```

`AgentProfile` gets `resources?: AgentResources;` (after `claude_code_tools?`). In `saveAgent` destructure `resources`
too and add `...(resources ? { resources } : {})` next to the `runtime` spread.

`services/admin-ui/app/lib/agentWizard.ts`: `import type { AgentResources, AgentRuntime, ClaudeCodeTool } from './profiles';`
and `export type NewAgent = AgentProposal & { runtime: AgentRuntime; resources: AgentResources; claude_code_tools: ClaudeCodeTool[] };`.

`services/admin-ui/app/components/AgentProfileSettings.tsx`: import `RESOURCE_MODES`, `RESOURCES_HELP` and
`type AgentResources` from `../lib/profiles`; directly after the `{!profile.is_coordinator && <Field><FieldLabel htmlFor="agent-type">Agent type</FieldLabel>…</Field>}`
block (it ends with `only the runtime changes.</FieldDescription></Field>}`) add:

```tsx
      {profile.is_coordinator ? <Field><FieldLabel>Resources</FieldLabel><p className="text-sm">Fixed</p>
        <FieldDescription>The coordinator works through the specialists it delegates to.</FieldDescription></Field>
        : <Field><FieldLabel htmlFor="agent-resources">Resources</FieldLabel>
        <Select value={draft.resources ?? 'fixed'} disabled={busy} onValueChange={value => change({ resources: value as AgentResources })}>
          <SelectTrigger id="agent-resources"><SelectValue /></SelectTrigger>
          <SelectContent><SelectGroup>{RESOURCE_MODES.map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectGroup></SelectContent></Select>
        <FieldDescription>{RESOURCES_HELP[draft.resources ?? 'fixed']}</FieldDescription></Field>}
```

`services/admin-ui/app/components/AgentWizard.tsx`:
- import `RESOURCE_MODES`, `RESOURCES_HELP`, `agentTypeLabel`, `resourcesLabel` and `type AgentResources` from
  `../lib/profiles`;
- after `const [runtime, setRuntime] = useState<AgentRuntime>('openai');` add
  `const [resources, setResources] = useState<AgentResources>('fixed');`;
- after the `agentType` constant add:

```tsx
  const resourcesField = <Field><FieldLabel htmlFor="wizard-resources">Resources</FieldLabel><Select value={resources} onValueChange={value => setResources(value as AgentResources)}><SelectTrigger id="wizard-resources"><SelectValue /></SelectTrigger><SelectContent><SelectGroup>{RESOURCE_MODES.map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectGroup></SelectContent></Select><FieldDescription>{RESOURCES_HELP[resources]}</FieldDescription></Field>;
```

- page 1: replace `{agentType}` (the first child of the `step === 'describe'` `FieldGroup`) with
  `<div className="grid gap-4 sm:grid-cols-2">{agentType}{resourcesField}</div>`;
- review General tab: replace `{agentType}` with

```tsx
          <p className="text-sm" aria-label="Agent type and resources"><span className="font-medium">{agentTypeLabel(runtime)} · {resourcesLabel(resources)}</span> <span className="text-muted-foreground">Change them on page 1 with Edit role.</span></p>
```

- `create`: `createWizardAgent({ ...proposal, runtime, resources, claude_code_tools: [...CLAUDE_CODE_TOOL_NAMES] })`.

`services/admin-ui/app/components/AgentsView.tsx`: import `resourcesLabel`; add `<TableHead>Resources</TableHead>`
after `<TableHead>Agent type</TableHead>`, the cell `<TableCell>{resourcesLabel(a.resources)}</TableCell>` after
`<TableCell>{agentTypeLabel(a.runtime)}</TableCell>`, `${resourcesLabel(a.resources)}` to the search text of
`filtered`, and change `colSpan={7}` to `colSpan={8}`.

- [ ] **Step 6: Run the tests and confirm they pass**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_agent_resources.py tests/test_knowledge_agents_config.py tests/test_profiles.py tests/test_agent_wizard.py tests/test_agent_versions.py tests/test_board.py tests/test_agent_sessions.py tests/test_claude_code_sessions.py -q`
Expected: PASS.

Run: `bash db/tests/run.sh`
Expected: every `PASS:` line, exit 0.

Run: `cd services/admin-ui && pnpm exec tsc --noEmit && pnpm exec vitest run app/components/__tests__/agentTypes.test.tsx app/components/__tests__/agentWizard.test.tsx app/components/__tests__/agentTabs.test.tsx`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add db/migrations/0075_agent_resources.up.sql db/migrations/0075_agent_resources.down.sql db/tests/run.sh \
  services/admin-api/src/knowledge_admin packages/llm-settings/src/knowledge_llm/profiles.py \
  services/admin-api/tests/test_agent_resources.py services/admin-ui/app
git commit -m "feat(agents): Resources setting, fixed or per run (0075)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Run resources — the request, its validation, the narrowed configuration and the record (0076)

**Files:**
- Create: `db/migrations/0076_run_resources.up.sql`, `db/migrations/0076_run_resources.down.sql`
- Modify: `db/tests/run.sh` (rollback pin `74` → `75`)
- Create: `services/admin-api/src/knowledge_admin/knowledge_agents/run_resources.py`
- Create: `services/admin-api/tests/test_run_resources.py`

**Interfaces:**
- Consumes: Task 1 (`config.per_run`, `resources` in the snapshot; wizard `resources`).
- Produces: `ka.agent_sessions_cc.run_resources`, `ka.agent_sessions.run_resources`, `ka.operations_runs.run_resources`
  (jsonb objects or NULL); in `knowledge_admin.knowledge_agents.run_resources`: `FIXED`, `NOT_USED`, `NOT_ALLOWED`,
  `NOT_AVAILABLE`, `class RunResourceError(ValueError)`, `class RunResources(BaseModel)` (`code_project_ids`,
  `collection_ids`, `server_ids`, `service_ids`: lists of UUID, each once), `resolve(conn, project_id, config,
  requested, *, environment_ids=()) -> dict` and `options(conn, project_id, config) -> dict[str, list[dict] | None]`
  (keys `code_projects`, `collections`, `servers`, `services`; items `{'id', 'name'}`; `None` for a kind the agent does
  not use). Test fixture `per_run` (in `test_run_resources.py`) used by Task 5.

- [ ] **Step 1: Write the failing tests**

Create `services/admin-api/tests/test_run_resources.py`:

```python
"""Run resources: a per-run agent's run gets exactly the resources it was given, validated against the agent's saved
selection; nothing passed means nothing, never "all". 0076 records them with each run."""
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import psycopg
import pytest

from knowledge_admin.agents import load_credentials
from knowledge_admin.knowledge_agents.config import get_agent_config
from knowledge_admin.knowledge_agents.run_resources import FIXED, RunResourceError, RunResources, options, resolve
from test_claude_code_sessions import native_agent, sessions  # noqa: F401  (fixture)
from test_operations import setup as ssh_setup
from test_repositories import HEADERS

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0076_run_resources.up.sql', '0076_run_resources.down.sql'
TABLES = {'agent_sessions_cc', 'agent_sessions', 'operations_runs'}
NOTHING = {'code_project_ids': [], 'collection_ids': [], 'server_ids': [], 'service_ids': []}


@pytest.fixture
def per_run(sessions):
    """A native per-run agent, Code analyst, that may be given the Payments API code project, the runbooks collection,
    Test host (SSH allowed, saved key, its own instructions) and the Payments service, which is also deployed on Test
    host. Not in its selection: Payments worker and the private collection. prod-1 does not allow SSH."""
    client, store = sessions.client, sessions.store
    host, key = ssh_setup(client)
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        conn.execute("INSERT INTO ka.rag_workspaces(project_id,settings) VALUES (%s,'{}')", (project,))
        collections = {name: str(conn.execute('''INSERT INTO ka.rag_collections(project_id,name,description)
            VALUES (%s,%s,%s) RETURNING id''', (project, name, f'The {name} documents')).fetchone()['id'])
            for name in ('runbooks', 'private')}
        conn.execute("""INSERT INTO ka.service_deployments(project_id,service_id,server_id,environment,runtime_name,port)
            VALUES (%s,%s,%s,'Staging','payments-api',8080)""", (project, sessions.service, host['id']))
    analyst = native_agent(client, 'Code analyst', model_id=sessions.gpt, resources='per_run',
                           repositories_enabled=True, repository_ids=[sessions.repo],
                           documents_enabled=True, collection_ids=[collections['runbooks']],
                           ssh_enabled=True, servers=[{'server_id': host['id'], 'instructions': 'Read logs only.',
                                                       'skill_ids': None, 'mcp_server_ids': None, 'connection': None}],
                           services_enabled=True, service_ids=[sessions.service])
    return SimpleNamespace(**vars(sessions), analyst=analyst, host=host, key=key,
                           runbooks=collections['runbooks'], private=collections['private'])


def effective(s, requested: RunResources | None, **kwargs) -> dict:
    with s.store.connect() as conn:
        return resolve(conn, s.store.project(conn, 'shop'), get_agent_config(s.analyst, conninfo=s.store.conninfo),
                       requested, **kwargs)


def refusal(s, requested: RunResources | None, **kwargs) -> str:
    with pytest.raises(RunResourceError) as raised:
        effective(s, requested, **kwargs)
    return str(raised.value)


def run_resource_columns(conn) -> set[str]:
    return {row['table_name'] for row in conn.execute("""SELECT table_name FROM information_schema.columns
        WHERE table_schema='ka' AND column_name='run_resources'""")}


def test_runs_record_their_resources_and_down_drops_only_the_records(store):
    with store.connect() as conn:
        assert run_resource_columns(conn) == TABLES
        for name in ('agent_sessions_cc_run_resources', 'agent_sessions_run_resources', 'operations_runs_run_resources'):
            definition = conn.execute('SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint WHERE conname=%s',
                                      (name,)).fetchone()['d']
            assert "jsonb_typeof(run_resources) = 'object'" in definition
        conn.execute((MIGRATIONS / DOWN).read_text(), prepare=False)
        conn.commit()
        assert run_resource_columns(conn) == set()
        conn.execute((MIGRATIONS / UP).read_text(), prepare=False)
        conn.commit()
        assert run_resource_columns(conn) == TABLES


def test_a_run_gets_exactly_its_resources_and_nothing_means_nothing(per_run):
    s = per_run
    given = effective(s, RunResources(code_project_ids=[s.repo], collection_ids=[s.runbooks], server_ids=[s.host['id']]))
    assert [p['name'] for p in given['code']['projects']] == ['Payments API'] and given['code']['all_projects'] is False
    assert [c['name'] for c in given['documents']['collections']] == ['runbooks']
    assert given['documents']['all_collections'] is False
    [server] = given['ssh']['servers']
    assert (server['id'], server['instructions'], bool(server['private_key_ref'])) == (s.host['id'], 'Read logs only.', True)
    assert given['services']['items'] == []
    assert given['run_resources'] == {'code_project_ids': [s.repo], 'collection_ids': [s.runbooks],
                                      'server_ids': [s.host['id']], 'service_ids': []}
    nothing = effective(s, None)
    assert (nothing['code']['projects'], nothing['documents']['collections'], nothing['ssh']['servers'],
            nothing['services']['items'], nothing['run_resources']) == ([], [], [], [], NOTHING)
    assert nothing['code']['enabled'] and nothing['documents']['enabled']  # their tools say nothing was given
    with s.store.connect() as conn:  # All code projects / all collections: any may be passed, still only those
        conn.execute('UPDATE ka.agents SET all_repositories=true,all_collections=true WHERE id=%s', (s.analyst,))
        conn.execute('DELETE FROM ka.agent_repositories WHERE agent_id=%s', (s.analyst,))
        conn.execute('DELETE FROM ka.agent_collections WHERE agent_id=%s', (s.analyst,))
    given = effective(s, RunResources(code_project_ids=[s.worker], collection_ids=[s.private]))
    assert [(p['name'], p['folder_paths']) for p in given['code']['projects']] == [
        ('Payments worker', [f'{s.data}/code/worker'])]
    assert [c['name'] for c in given['documents']['collections']] == ['private']
    assert given['documents']['all_collections'] is False and given['code']['all_projects'] is False
    nothing = effective(s, None)
    assert (nothing['code']['projects'], nothing['documents']['collections']) == ([], [])
    assert get_agent_config(s.analyst, conninfo=s.store.conninfo)['code']['all_projects'] is True  # the agent's own


def test_resources_outside_the_selection_the_workspace_or_the_agents_kinds_are_refused(per_run):
    s = per_run
    other = s.client.post('/api/servers', headers=HEADERS,
                          json={'name': 'other-host', 'hostname': 'other.test', 'ssh_allowed': True}).json()
    unknown = str(uuid4())
    assert refusal(s, RunResources(code_project_ids=[s.worker])) == 'Code project Payments worker is not allowed for this agent.'
    assert refusal(s, RunResources(collection_ids=[s.private])) == 'Collection private is not allowed for this agent.'
    assert refusal(s, RunResources(server_ids=[other['id']])) == 'Server other-host is not allowed for this agent.'
    assert refusal(s, RunResources(code_project_ids=[unknown])) == (
        f'Code project {unknown} is not available: it was removed, archived or disabled.')
    assert refusal(s, RunResources(server_ids=[s.server['id']])) == (
        'Server prod-1 is not available: it was removed, archived or disabled.')  # SSH not allowed
    with s.store.connect() as conn:
        conn.execute('UPDATE ka.repositories SET archived_at=now() WHERE id=%s', (s.repo,))
    assert refusal(s, RunResources(code_project_ids=[s.repo])) == (
        'Code project Payments API is not available: it was removed, archived or disabled.')
    with s.store.connect() as conn:
        conn.execute('UPDATE ka.repositories SET archived_at=NULL WHERE id=%s', (s.repo,))
        conn.execute('UPDATE ka.agents SET documents_enabled=false WHERE id=%s', (s.analyst,))
    assert refusal(s, RunResources(collection_ids=[s.runbooks])) == 'This agent does not use document collections.'
    with s.store.connect() as conn:
        conn.execute("UPDATE ka.agents SET resources='fixed' WHERE id=%s", (s.analyst,))
        config = get_agent_config(s.analyst, conninfo=s.store.conninfo)
        assert resolve(conn, s.store.project(conn, 'shop'), config, None) is config  # fixed: its own configuration
    assert refusal(s, RunResources()) == FIXED


def test_services_and_environments_bring_their_code_projects_and_servers(per_run):
    s = per_run
    assert refusal(s, RunResources(service_ids=[s.service])) == 'Code project Payments worker is not allowed for this agent.'
    with s.store.connect() as conn:
        conn.execute('INSERT INTO ka.agent_repositories(agent_id,repository_id) VALUES (%s,%s)', (s.analyst, s.worker))
    assert effective(s, RunResources(service_ids=[s.service]))['run_resources'] == {
        'code_project_ids': [s.repo, s.worker], 'collection_ids': [], 'server_ids': [s.host['id']],
        'service_ids': [s.service]}  # prod-1 does not allow SSH: skipped
    with s.store.connect() as conn:
        conn.execute('UPDATE ka.agents SET services_enabled=false WHERE id=%s', (s.analyst,))
    assert refusal(s, RunResources(service_ids=[s.service])) == 'This agent does not use services.'
    assert effective(s, None, environment_ids=[UUID(s.environment)])['run_resources'] == {
        'code_project_ids': [s.repo, s.worker], 'collection_ids': [], 'server_ids': [s.host['id']], 'service_ids': []}


def test_credentials_are_decrypted_only_for_the_servers_a_run_was_given(per_run):
    s = per_run
    without = effective(s, RunResources(code_project_ids=[s.repo]))
    with_host = effective(s, RunResources(server_ids=[s.host['id']]))
    key_id = with_host['ssh']['servers'][0]['private_key_ref']['id']
    assert key_id not in load_credentials(s.store, without)
    assert load_credentials(s.store, with_host)[key_id] == s.key['private_key']


def test_options_list_what_a_caller_may_pass(per_run):
    s = per_run

    def offered() -> dict:
        with s.store.connect() as conn:
            return options(conn, s.store.project(conn, 'shop'), get_agent_config(s.analyst, conninfo=s.store.conninfo))
    assert offered() == {'code_projects': [{'id': s.repo, 'name': 'Payments API'}],
                         'collections': [{'id': s.runbooks, 'name': 'runbooks'}],
                         'servers': [{'id': s.host['id'], 'name': 'Test host'}],
                         'services': [{'id': s.service, 'name': 'Payments'}]}
    with s.store.connect() as conn:
        conn.execute('UPDATE ka.agents SET all_repositories=true,documents_enabled=false WHERE id=%s', (s.analyst,))
        conn.execute('DELETE FROM ka.agent_repositories WHERE agent_id=%s', (s.analyst,))
    assert offered()['code_projects'] == [{'id': s.repo, 'name': 'Payments API'}, {'id': s.worker, 'name': 'Payments worker'}]
    assert offered()['collections'] is None
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_run_resources.py -q`
Expected: FAIL: `ModuleNotFoundError: No module named 'knowledge_admin.knowledge_agents.run_resources'`.

- [ ] **Step 3: Implement the migration**

Create `db/migrations/0076_run_resources.up.sql`:

```sql
BEGIN;
SET search_path = ka, public;

-- What each run of a per-run agent was given (code projects, collections, servers and services, by id), on the
-- record of the run: Generate documents sessions, test chats and their turns. NULL for runs of fixed agents.
ALTER TABLE agent_sessions_cc ADD COLUMN run_resources jsonb
    CONSTRAINT agent_sessions_cc_run_resources CHECK (run_resources IS NULL OR jsonb_typeof(run_resources) = 'object');
ALTER TABLE agent_sessions ADD COLUMN run_resources jsonb
    CONSTRAINT agent_sessions_run_resources CHECK (run_resources IS NULL OR jsonb_typeof(run_resources) = 'object');
ALTER TABLE operations_runs ADD COLUMN run_resources jsonb
    CONSTRAINT operations_runs_run_resources CHECK (run_resources IS NULL OR jsonb_typeof(run_resources) = 'object');
COMMENT ON COLUMN agent_sessions_cc.run_resources IS 'Per-run agents: the code projects, collections, servers and services this session was given.';
COMMENT ON COLUMN agent_sessions.run_resources IS 'Per-run agents: the resources this conversation was given (fixed for the conversation).';
COMMENT ON COLUMN operations_runs.run_resources IS 'Per-run agents: the resources this turn ran with.';

COMMIT;
```

Create `db/migrations/0076_run_resources.down.sql`:

```sql
BEGIN;
SET search_path = ka, public;

-- Only the records go: pinned configurations (agent_sessions.config, agent_sessions_cc.agent_config) keep their
-- narrowed lists and run_resources, so no conversation gains access.
ALTER TABLE operations_runs DROP COLUMN run_resources;
ALTER TABLE agent_sessions DROP COLUMN run_resources;
ALTER TABLE agent_sessions_cc DROP COLUMN run_resources;

COMMIT;
```

`db/tests/run.sh`: change `[ "$rolled_back_version" = 74 ]` to `[ "$rolled_back_version" = 75 ]`.

- [ ] **Step 4: Implement `run_resources.py`**

Create `services/admin-api/src/knowledge_admin/knowledge_agents/run_resources.py`:

```python
"""Run resources: what one run of a per-run agent works on, and the configuration that run uses.

A per-run agent's saved selections (code projects, collections, SSH servers, services) are what a caller may pass;
each run passes its own. `resolve` checks the request and narrows each resource list of the agent's snapshot to it,
so the existing tools, which only reach what the configuration lists, reach exactly what the run was given. Nothing
passed for a kind leaves that list empty: never "all". Fixed agents keep their configuration and refuse run
resources. Credentials stay references; nothing is decrypted here."""
import copy
import os
from collections.abc import Iterable
from pathlib import PurePosixPath
from typing import Any, NamedTuple
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field, field_validator

from knowledge_admin.collections import document_collections
from knowledge_admin.data_root import data_path
from knowledge_admin.knowledge_agents.config import per_run

FIXED = 'This agent uses fixed resources.'
NOT_USED = 'This agent does not use {what}.'
NOT_ALLOWED = '{label} {name} is not allowed for this agent.'
NOT_AVAILABLE = '{label} {name} is not available: it was removed, archived or disabled.'


class Kind(NamedTuple):
    key: str         # the run resources key
    capability: str  # the config section with its switch
    label: str       # one resource, in messages
    plural: str      # the kind, in messages
    option: str      # the options() key


KINDS = (Kind('code_project_ids', 'code', 'Code project', 'code projects', 'code_projects'),
         Kind('collection_ids', 'documents', 'Collection', 'document collections', 'collections'),
         Kind('server_ids', 'ssh', 'Server', 'servers', 'servers'),
         Kind('service_ids', 'services', 'Service', 'services', 'services'))

# The id, name and whether it can be given now, of the requested resources of one kind in this workspace.
FOUND = {
    'code_project_ids': 'SELECT id,name,archived_at IS NULL AS usable FROM ka.repositories WHERE project_id=%s AND id=ANY(%s)',
    'collection_ids': 'SELECT id,name,deleted_at IS NULL AS usable FROM ka.rag_collections WHERE project_id=%s AND id=ANY(%s)',
    'server_ids': 'SELECT id,name,ssh_allowed AS usable FROM ka.servers WHERE project_id=%s AND id=ANY(%s)',
    'service_ids': 'SELECT id,name,true AS usable FROM ka.services WHERE project_id=%s AND id=ANY(%s)',
}
# Every usable resource of one kind in this workspace (what "all" allows, and the options a caller sees).
LISTED = {
    'code_project_ids': 'SELECT id,name FROM ka.repositories WHERE project_id=%s AND archived_at IS NULL ORDER BY lower(name),id',
    'collection_ids': 'SELECT id,name FROM ka.rag_collections WHERE project_id=%s AND deleted_at IS NULL ORDER BY name,id',
    'server_ids': 'SELECT id,name FROM ka.servers WHERE project_id=%s AND ssh_allowed ORDER BY lower(name),id',
    'service_ids': 'SELECT id,name FROM ka.services WHERE project_id=%s ORDER BY lower(name),id',
}
CODE_PROJECTS = """SELECT r.id,r.name,r.description,r.remote_url AS remote_git_url,
    ARRAY(SELECT f.path::text FROM ka.code_project_folders f WHERE f.code_project_id=r.id ORDER BY f.path) AS folder_paths
    FROM ka.repositories r WHERE r.project_id=%s AND r.id=ANY(%s) AND r.archived_at IS NULL"""
COLLECTIONS = """SELECT c.id,c.name,c.description,
    ARRAY(SELECT p.source_path FROM ka.rag_collection_paths p WHERE p.collection_id=c.id ORDER BY p.source_path) AS source_paths
    FROM ka.rag_collections c WHERE c.project_id=%s AND c.id=ANY(%s) AND c.deleted_at IS NULL"""
# What services and environments bring: only usable code projects (not archived) and servers (SSH allowed).
ENVIRONMENT_SERVICES = '''SELECT es.service_id AS id FROM ka.environment_services es JOIN ka.services s ON s.id=es.service_id
    WHERE es.project_id=%s AND es.environment_id=ANY(%s) ORDER BY lower(s.name),s.id'''
ENVIRONMENT_SERVERS = '''SELECT es.server_id AS id FROM ka.environment_servers es JOIN ka.servers s ON s.id=es.server_id
    WHERE es.project_id=%s AND es.environment_id=ANY(%s) AND s.ssh_allowed ORDER BY lower(s.name),s.id'''
SERVICE_CODE = '''SELECT r.id FROM ka.service_repositories sr JOIN ka.repositories r ON r.id=sr.repository_id
    WHERE r.project_id=%s AND sr.service_id=ANY(%s) AND r.archived_at IS NULL ORDER BY lower(r.name),r.id'''
SERVICE_SERVERS = '''SELECT h.id FROM ka.service_deployments d JOIN ka.servers h ON h.id=d.server_id
    WHERE h.project_id=%s AND d.service_id=ANY(%s) AND h.ssh_allowed ORDER BY lower(h.name),h.id'''


class RunResourceError(ValueError):
    """The run cannot be given a resource; the message names it."""


class RunResources(BaseModel):
    """What a caller passes to one run of a per-run agent: each id once, in the caller's order."""
    model_config = ConfigDict(extra='forbid')
    code_project_ids: list[UUID] = Field(default_factory=list, max_length=50)
    collection_ids: list[UUID] = Field(default_factory=list, max_length=200)
    server_ids: list[UUID] = Field(default_factory=list, max_length=100)
    service_ids: list[UUID] = Field(default_factory=list, max_length=100)

    @field_validator('code_project_ids', 'collection_ids', 'server_ids', 'service_ids')
    @classmethod
    def once(cls, value: list[UUID]) -> list[UUID]:
        return list(dict.fromkeys(value))


def _ids(rows: Iterable[dict[str, Any]]) -> list[str]:
    return [str(row['id']) for row in rows]


def _selection(config: dict[str, Any], kind: Kind) -> dict[str, dict[str, Any]] | None:
    """The agent's saved selection of one kind, by id, as its snapshot lists it; None when it allows any (All code
    projects, All collections)."""
    if kind.capability == 'code':
        return None if config['code'].get('all_projects') else {str(item['id']): item for item in config['code']['projects']}
    if kind.capability == 'documents':
        everything, items = document_collections(config)
        return None if everything else {str(item['id']): item for item in items if 'id' in item}
    if kind.capability == 'ssh':
        return {str(item['id']): item for item in config['ssh']['servers']}
    return {str(item['id']): item for item in config['services']['items']}


def _fresh(conn, project_id, kind: Kind, ids: list[str]) -> dict[str, dict[str, Any]]:
    """Code projects or collections an "all" selection allows, in the snapshot's shape (container paths)."""
    if kind.capability == 'code':
        rows = conn.execute(CODE_PROJECTS, (project_id, ids)).fetchall()
        root = PurePosixPath(os.environ.get('CODE_ROOT', ''))
        if rows and (root.anchor != '/' or '..' in root.parts):
            raise ValueError('CODE_ROOT must be an absolute path mounted in the agent container')
        for row in rows:
            row['folder_paths'] = [str(root / path) for path in row['folder_paths']]
            if row['remote_git_url'] is None:
                del row['remote_git_url']
    else:
        rows = conn.execute(COLLECTIONS, (project_id, ids)).fetchall()
        for row in rows:
            row['paths'] = [str(data_path(path)) for path in row.pop('source_paths')]
    return {str(row['id']): jsonable_encoder(row) for row in rows}


def resolve(conn, project_id, config: dict[str, Any], requested: RunResources | None, *,
            environment_ids: Iterable[UUID] = ()) -> dict[str, Any]:
    """The configuration one run uses.

    Fixed agents: their own configuration, unchanged; passing run resources is refused (FIXED).
    Per-run agents: a copy whose code projects, collections, SSH servers and services are exactly the run's resources,
    with `run_resources` recording them. A chosen service brings its code projects and the servers it is deployed on;
    an environment (Generate documents) brings its services and servers, and through them their code projects and
    servers. What they bring is added only for kinds the agent uses, and only when it can be given (not archived, SSH
    allowed). Every resource must exist in this workspace, be usable, be of a kind the agent uses and be inside the
    agent's saved selection; otherwise RunResourceError names it.
    """
    if not per_run(config):
        if requested is not None:
            raise RunResourceError(FIXED)
        return config
    effective = copy.deepcopy(config)
    requested = requested or RunResources()
    wanted = {kind.key: [str(item) for item in getattr(requested, kind.key)] for kind in KINDS}
    for kind in KINDS:
        if wanted[kind.key] and not effective[kind.capability]['enabled']:
            raise RunResourceError(NOT_USED.format(what=kind.plural))
    environments = [str(item) for item in environment_ids]
    brought: dict[str, list[str]] = {'code_project_ids': [], 'server_ids': [], 'service_ids': []}
    if environments:
        brought['service_ids'] = _ids(conn.execute(ENVIRONMENT_SERVICES, (project_id, environments)))
        brought['server_ids'] = _ids(conn.execute(ENVIRONMENT_SERVERS, (project_id, environments)))
    services = list(dict.fromkeys([*wanted['service_ids'], *brought['service_ids']]))
    if services:
        brought['code_project_ids'] = _ids(conn.execute(SERVICE_CODE, (project_id, services)))
        brought['server_ids'] += _ids(conn.execute(SERVICE_SERVERS, (project_id, services)))
    for kind in KINDS:
        if kind.key in brought and effective[kind.capability]['enabled']:
            wanted[kind.key] = list(dict.fromkeys([*wanted[kind.key], *brought[kind.key]]))
    given: dict[str, list[dict[str, Any]]] = {}
    for kind in KINDS:
        ids = wanted[kind.key]
        found = {str(row['id']): row for row in conn.execute(FOUND[kind.key], (project_id, ids))} if ids else {}
        selection = _selection(effective, kind)
        for item in ids:
            row = found.get(item)
            if row is None or not row['usable']:
                raise RunResourceError(NOT_AVAILABLE.format(label=kind.label, name=row['name'] if row else item))
            if selection is not None and item not in selection:
                raise RunResourceError(NOT_ALLOWED.format(label=kind.label, name=row['name']))
            if kind.capability == 'ssh' and not (selection[item]['enabled'] and selection[item]['ssh_allowed']):
                raise RunResourceError(NOT_AVAILABLE.format(label=kind.label, name=row['name']))
        source = _fresh(conn, project_id, kind, ids) if selection is None and ids else (selection or {})
        given[kind.key] = [source[item] for item in ids]
    code_ids = set(wanted['code_project_ids'])
    effective['code'] = {**effective['code'], 'all_projects': False, 'projects': given['code_project_ids']}
    if 'graphs' in effective['code']:
        effective['code']['graphs'] = [graph for graph in effective['code']['graphs'] if str(graph.get('id')) in code_ids]
    documents = {key: value for key, value in effective['documents'].items() if key not in ('folders', 'all_folders')}
    effective['documents'] = {**documents, 'all_collections': False, 'collections': given['collection_ids']}
    effective['ssh'] = {**effective['ssh'], 'servers': given['server_ids']}
    effective['services'] = {**effective['services'], 'items': given['service_ids']}
    effective['run_resources'] = wanted
    return jsonable_encoder(effective)


def options(conn, project_id, config: dict[str, Any]) -> dict[str, list[dict[str, str]] | None]:
    """What a caller may pass to one run of a per-run agent, per kind: the usable resources inside its saved selection
    (every usable one for All), as id and name; None for a kind the agent does not use."""
    result: dict[str, list[dict[str, str]] | None] = {}
    for kind in KINDS:
        if not config[kind.capability]['enabled']:
            result[kind.option] = None
            continue
        selection = _selection(config, kind)
        result[kind.option] = [
            {'id': str(row['id']), 'name': row['name']} for row in conn.execute(LISTED[kind.key], (project_id,))
            if selection is None or str(row['id']) in selection and (
                kind.capability != 'ssh' or selection[str(row['id'])]['enabled'])]
    return result
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_run_resources.py tests/test_agent_resources.py tests/test_native_sessions_migration.py tests/test_agent_sessions.py -q`
Expected: PASS.

Run: `bash db/tests/run.sh`
Expected: every `PASS:` line, exit 0.

- [ ] **Step 6: Commit**

```bash
git add db/migrations/0076_run_resources.up.sql db/migrations/0076_run_resources.down.sql db/tests/run.sh \
  services/admin-api/src/knowledge_admin/knowledge_agents/run_resources.py services/admin-api/tests/test_run_resources.py
git commit -m "feat(agents): run resources, validated and narrowed per run, recorded with runs (0076)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Tools refuse what the run was not given

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/tools/__init__.py`
- Modify: `services/admin-api/src/knowledge_admin/tools/files.py` (`_roots`)
- Modify: `services/admin-api/src/knowledge_admin/tools/documents.py` (`_allowed`)
- Modify: `services/admin-api/src/knowledge_admin/tools/code_search.py`, `ast_grep.py`, `code_graph.py`, `ssh.py`
- Modify: `services/admin-api/src/knowledge_admin/agents.py` (`get_prompt`)
- Create: `services/admin-api/tests/test_run_resource_tools.py`

**Interfaces:**
- Consumes: Task 1 (`per_run`, `require_run_resources`, `NO_RUN_RESOURCES`); the narrowed shape of Task 2.
- Produces: `tools.NOTHING_GIVEN: dict[str, str]` (keys `code`, `documents`, `ssh`, `files`) and
  `tools.require_given(config, kind) -> None`.

- [ ] **Step 1: Write the failing tests**

Create `services/admin-api/tests/test_run_resource_tools.py`:

```python
"""Per-run agents' tools reach exactly what the run was given: anything else is refused like any resource outside an
agent's configuration, and a kind the run got nothing of is refused, never widened to every resource."""
import asyncio
import json

import pytest
from agents.tool_context import ToolContext

from knowledge_admin.agents import get_prompt
from knowledge_admin.knowledge_agents.config import NO_RUN_RESOURCES
from knowledge_admin.tools import NOTHING_GIVEN
from knowledge_admin.tools.ast_grep import ast_grep
from knowledge_admin.tools.code_graph import code_graph
from knowledge_admin.tools.code_search import code_search
from knowledge_admin.tools.documents import read_document, search_documents
from knowledge_admin.tools.files import list_files, read_file
from knowledge_admin.tools.shell import shell_run
from knowledge_admin.tools.ssh import ssh_run
from test_document_tools import rag  # noqa: F401  (fixture)

PATTERN = 'def $F($$$A): $$$B'


def invoke(tool, context, **arguments) -> str:
    payload = json.dumps(arguments)
    wrapped = ToolContext(context=context, tool_name=tool.name, tool_call_id='t1', tool_arguments=payload)
    return asyncio.run(tool.on_invoke_tool(wrapped, payload))


@pytest.fixture
def folders(tmp_path):
    for name in ('workspace', 'given', 'other'):
        (tmp_path / name).mkdir()
    (tmp_path / 'given' / 'a.py').write_text('def given():\n    return 1\n', encoding='utf-8')
    (tmp_path / 'other' / 'b.py').write_text('def other():\n    return 2\n', encoding='utf-8')
    return {name: (tmp_path / name).resolve() for name in ('workspace', 'given', 'other')}


def run_context(folders, *, code=(), collections=(), servers=()) -> dict:
    """A per-run agent's tool context with the configuration resolve() builds: the lists are the run's resources."""
    host = {'id': 'host', 'name': 'Host', 'hostname': 'host.test', 'port': 22, 'username': 'operator',
            'enabled': True, 'ssh_allowed': True, 'allow_sudo': False, 'known_hosts': '', 'private_key_ref': {'id': 'key'}}
    config = {
        'id': 'analyst', 'name': 'Code analyst', 'description': '', 'enabled': True, 'resources': 'per_run',
        'instructions': '', 'system_prompt': {'content': 'Analyse code.'},
        'code': {'enabled': True, 'all_projects': False, 'projects': [
            {'id': 'given', 'name': 'Given', 'folder_paths': [str(folders['given'])]}] if 'given' in code else []},
        'documents': {'enabled': True, 'grounded': False, 'all_collections': False, 'collections': [
            {'id': '1', 'name': 'payments', 'description': '', 'paths': []}] if 'payments' in collections else []},
        'ssh': {'enabled': True, 'servers': [host] if 'host' in servers else []},
        'skills': {'enabled': False, 'items': []}, 'mcp': {'enabled': False, 'servers': []},
        'services': {'enabled': False, 'items': []},
        'run_resources': {'code_project_ids': list(code), 'collection_ids': list(collections),
                          'server_ids': list(servers), 'service_ids': []}}
    return {'config': config, 'credentials': {}, 'workspace': folders['workspace'], 'shell_enabled': True}


def test_file_and_code_tools_reach_only_the_given_code_projects(folders):
    context = run_context(folders, code=['given'])
    roots = json.loads(invoke(list_files, context))['entries']
    assert {entry['path'] for entry in roots} == {str(folders['workspace']), str(folders['given'])}
    assert 'def given' in invoke(read_file, context, path=str(folders['given'] / 'a.py'))
    outside = 'outside the agent’s selected folders'
    assert outside in invoke(read_file, context, path=str(folders['other'] / 'b.py'))
    assert outside in invoke(code_search, context, path=str(folders['other']), language='Python', name='other')
    assert outside in invoke(ast_grep, context, path=str(folders['other']), language='Python', pattern=PATTERN)


def test_a_kind_the_run_got_nothing_of_is_refused_never_widened(folders, monkeypatch):
    monkeypatch.setenv('AGENT_SHELL_URL', 'http://shell.invalid')  # refused before any request
    context = run_context(folders)
    assert NOTHING_GIVEN['files'] in invoke(list_files, context)
    assert NOTHING_GIVEN['files'] in invoke(shell_run, context, command='ls /')
    assert NOTHING_GIVEN['code'] in invoke(code_search, context, path=str(folders['given']), language='Python', name='given')
    assert NOTHING_GIVEN['code'] in invoke(ast_grep, context, path=str(folders['given']), language='Python', pattern=PATTERN)
    assert NOTHING_GIVEN['code'] in invoke(code_graph, context, project_id='given')
    assert NOTHING_GIVEN['ssh'] in invoke(ssh_run, context, server_id='host', command='uptime')
    assert NOTHING_GIVEN['documents'] in invoke(search_documents, context, query='limits')
    assert NOTHING_GIVEN['documents'] in invoke(read_document, context, collection='payments', path='limits.md')
    unstarted = run_context(folders, code=['given'])
    del unstarted['config']['run_resources']
    assert NO_RUN_RESOURCES in invoke(list_files, unstarted)


def test_document_search_uses_only_the_given_collections(rag, folders):
    calls, _ = rag
    context = run_context(folders, collections=['payments'])
    context['config']['documents']['all_collections'] = True  # even a stray "all" stays what the run was given
    assert '[1] payments/retries.md' in invoke(search_documents, context, query='retries')
    search = next(json.loads(call.content) for call in calls if call.url.path == '/search')
    assert search['collections'] == ['payments']
    assert 'outside the agent’s document collections' in invoke(search_documents, context, query='x', collection='private')


def test_ssh_reaches_only_the_given_servers(folders):
    context = run_context(folders, servers=['host'])
    assert 'This server is not available to this agent.' in invoke(ssh_run, context, server_id='elsewhere', command='uptime')


def test_the_prompt_says_the_run_got_only_these_resources(folders):
    prompt = get_prompt(run_context(folders, code=['given'])['config'], [], [])
    assert 'This run was given only the resources listed below.' in prompt
    assert str(folders['given']) in prompt and str(folders['other']) not in prompt
    fixed = run_context(folders, code=['given'])['config'] | {'resources': 'fixed'}
    assert 'This run was given only' not in get_prompt(fixed, [], [])
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_run_resource_tools.py -q`
Expected: FAIL: `ImportError: cannot import name 'NOTHING_GIVEN' from 'knowledge_admin.tools'`.

- [ ] **Step 3: Implement**

`services/admin-api/src/knowledge_admin/tools/__init__.py`: add the imports
`from knowledge_admin.collections import document_collections` and
`from knowledge_admin.knowledge_agents.config import per_run, require_run_resources`, then after `AgentContext`:

```python
NOTHING_GIVEN = {
    'code': 'No code project was given for this run.',
    'documents': 'No document collection was given for this run.',
    'ssh': 'No server was given for this run.',
    'files': 'No code project or document collection was given for this run.',
}


def require_given(config: dict[str, Any], kind: str) -> None:
    """Per-run agents: refuse a tool whose kind the run was given nothing of. Their configuration lists exactly the
    run's resources, so an empty list means nothing, never every resource. Fixed agents are unaffected."""
    if not per_run(config):
        return
    require_run_resources(config)
    code = config['code']['projects'] if config['code']['enabled'] else []
    collections = document_collections(config)[1] if config['documents']['enabled'] else []
    empty = {'code': not code, 'documents': not collections, 'ssh': not config['ssh']['servers'],
             'files': not code and not collections}
    if empty[kind]:
        raise ValueError(NOTHING_GIVEN[kind])
```

`services/admin-api/src/knowledge_admin/tools/files.py`: import `require_given` with `AgentContext`
(`from knowledge_admin.tools import AgentContext, require_given`); in `_roots` after the disabled check add
`require_given(config, 'files')`.

`services/admin-api/src/knowledge_admin/tools/documents.py`: import `require_given` and
`from knowledge_admin.knowledge_agents.config import per_run`; `_allowed` becomes:

```python
def _allowed(config: dict[str, Any]) -> list[str] | None:
    """Collection names the agent may use; None means every collection (never for a per-run run)."""
    if not config['enabled'] or not config['documents']['enabled']:
        raise ValueError('Document search is not enabled for this agent.')
    require_given(config, 'documents')
    everything, items = document_collections(config)
    return None if everything and not per_run(config) else sorted({item['name'] for item in items})
```

`code_search.py`, `ast_grep.py` and `code_graph.py`: import `require_given` from `knowledge_admin.tools` and directly
after each `raise ValueError('Code access is disabled.')` check add `require_given(<config>, 'code')`, where `<config>`
is `ctx.context['config']` (`code_search`, `ast_grep`) or `config` (`code_graph`).

`ssh.py`: import `require_given`; after `raise ValueError('SSH is disabled for this agent.')` add
`require_given(config, 'ssh')`.

`services/admin-api/src/knowledge_admin/agents.py`, `get_prompt`: import `per_run` from
`knowledge_admin.knowledge_agents.config`; directly before `prompt += '\n\nUse only the configured resources and tools. '`
add:

```python
    if per_run(config):
        prompt += '\n\nThis run was given only the resources listed below. Ask for anything else instead of looking for it.'
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_run_resource_tools.py tests/test_function_tools.py tests/test_document_tools.py tests/test_code_search.py tests/test_ast_grep.py tests/test_code_graph_sdk.py tests/test_agent_ssh.py tests/test_sdk_agent.py -q`
Expected: PASS (fixed agents' tools are unchanged).

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/tools services/admin-api/src/knowledge_admin/agents.py \
  services/admin-api/tests/test_run_resource_tools.py
git commit -m "feat(tools): per-run agents' tools refuse what the run was not given

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Test chats run with run resources, fixed for the conversation

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/agent_sessions.py`
- Modify: `services/admin-api/src/knowledge_admin/profiles_api.py` (`GET /agents/{id}/run-resources`)
- Modify: `services/admin-api/src/knowledge_admin/agent_versions.py` (`source` for a review)
- Modify: `services/admin-api/tests/test_agent_resources.py` (delete the interim Task 1 refusal test)
- Create: `services/admin-api/tests/test_run_resources_test_chats.py`

**Interfaces:**
- Consumes: Task 2 (`RunResources`, `resolve`, `options`, `RunResourceError`, the 0076 columns); Task 3 (tools).
- Produces: `RunInput.resources: RunResources | None` and `SessionInput.resources: RunResources | None` on
  `POST /api/agents/{id}/runs` and `POST /api/agents/{id}/sessions` (first message / new session only);
  `agent_sessions.KEEPS_RESOURCES`; `ka.agent_sessions.run_resources` and `ka.operations_runs.run_resources` written;
  session detail (`SELECT s.*`) returns `run_resources`; `GET /api/agents/{id}/run-resources?version_id=` →
  `{'resources': 'fixed'}` or `{'resources': 'per_run', 'code_projects': [...] | None, 'collections': ...,
  'servers': ..., 'services': ...}`.

- [ ] **Step 1: Write the failing tests**

Create `services/admin-api/tests/test_run_resources_test_chats.py`:

```python
"""Test chats of a per-run agent: the first message passes the run resources, the conversation pins the narrowed
configuration and records them, and follow-ups cannot change them."""
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from knowledge_admin.agent_sessions import KEEPS_RESOURCES
from knowledge_admin.knowledge_agents.run_resources import FIXED
from test_agent_runs import direct, events  # noqa: F401  (fixture)
from test_sdk_agent import responses_api  # noqa: F401  (fixture)
from test_repositories import HEADERS


@pytest.fixture
def analyst(direct, store, tmp_path):
    """The direct test agent as a per-run agent that may be given any code project: App and Worker."""
    client, agent_id, path = direct
    app, worker = uuid4(), uuid4()
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        for project_id, slug in ((app, 'app'), (worker, 'worker')):
            (tmp_path / slug).mkdir()
            conn.execute('INSERT INTO ka.repositories(id,project_id,slug,name) VALUES (%s,%s,%s,%s)',
                         (project_id, project, slug, slug.title()))
            conn.execute('INSERT INTO ka.code_project_folders(code_project_id,project_id,path) VALUES (%s,%s,%s)',
                         (project_id, project, slug))
        conn.execute("UPDATE ka.agents SET resources='per_run',repositories_enabled=true,all_repositories=true WHERE id=%s",
                     (agent_id,))
    return SimpleNamespace(client=client, agent=agent_id, path=path, app=str(app), worker=str(worker), root=tmp_path.resolve())


def test_a_test_chat_runs_with_exactly_the_resources_it_started_with(analyst, store, responses_api):
    a = analyst
    first = a.client.post(a.path, headers=HEADERS, json={'task': 'Where are the limits?',
                                                          'resources': {'code_project_ids': [a.app]}})
    started, *_, completed = events(first)
    assert completed['status'] == 'completed', completed
    config = completed['context']['config']
    assert [p['id'] for p in config['code']['projects']] == [a.app]
    assert config['run_resources'] == {'code_project_ids': [a.app], 'collection_ids': [], 'server_ids': [], 'service_ids': []}
    prompt = json.dumps(responses_api[1][-1]['body'])
    assert f'{a.root}/app' in prompt and f'{a.root}/worker' not in prompt
    session = started['session_id']
    with store.connect() as conn:
        assert conn.execute('SELECT run_resources FROM ka.agent_sessions WHERE id=%s', (session,)).fetchone()[
            'run_resources'] == config['run_resources']
        assert conn.execute('SELECT run_resources FROM ka.operations_runs WHERE id=%s', (completed['id'],)).fetchone()[
            'run_resources'] == config['run_resources']
    follow = events(a.client.post(a.path, headers=HEADERS, json={'task': 'And the callers?', 'session_id': session}))[-1]
    assert follow['context']['config']['run_resources']['code_project_ids'] == [a.app]
    changed = a.client.post(a.path, headers=HEADERS, json={'task': 'Now the worker', 'session_id': session,
                                                           'resources': {'code_project_ids': [a.worker]}})
    assert changed.status_code == 409 and changed.json()['error']['message'] == KEEPS_RESOURCES
    detail = a.client.get(f'/api/agents/{a.agent}/sessions/{session}', headers=HEADERS).json()
    assert detail['run_resources']['code_project_ids'] == [a.app]


def test_test_chats_refuse_resources_a_per_run_agent_may_not_get_and_fixed_agents_any(analyst, store):
    a = analyst
    with store.connect() as conn:
        conn.execute('UPDATE ka.agents SET all_repositories=false WHERE id=%s', (a.agent,))
        conn.execute('INSERT INTO ka.agent_repositories(agent_id,repository_id) VALUES (%s,%s)', (a.agent, a.app))
    refused = a.client.post(a.path, headers=HEADERS, json={'task': 'x', 'resources': {'code_project_ids': [a.worker]}})
    assert refused.status_code == 400 and refused.json()['error']['message'] == 'Code project Worker is not allowed for this agent.'
    created = a.client.post(f'/api/agents/{a.agent}/sessions', headers=HEADERS, json={
        'title': 'Check', 'is_test': True, 'resources': {'server_ids': [str(uuid4())]}})
    assert created.status_code == 400 and created.json()['error']['message'] == 'This agent does not use servers.'
    nothing = events(a.client.post(a.path, headers=HEADERS, json={'task': 'What can you read?'}))[-1]
    assert nothing['status'] == 'completed'
    assert nothing['context']['config']['code']['projects'] == []  # nothing passed: nothing, never all
    assert nothing['context']['config']['run_resources']['code_project_ids'] == []
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET resources='fixed' WHERE id=%s", (a.agent,))
    fixed = a.client.post(a.path, headers=HEADERS, json={'task': 'x', 'resources': {}})
    assert fixed.status_code == 400 and fixed.json()['error']['message'] == FIXED
    run = events(a.client.post(a.path, headers=HEADERS, json={'task': 'Fixed run'}))[-1]
    with store.connect() as conn:
        assert conn.execute('SELECT run_resources FROM ka.operations_runs WHERE id=%s', (run['id'],)).fetchone()[
            'run_resources'] is None


def test_the_test_tab_offers_what_a_run_may_be_given(analyst, store):
    a = analyst
    offered = a.client.get(f'/api/agents/{a.agent}/run-resources', headers=HEADERS)
    assert offered.status_code == 200 and offered.headers['cache-control'] == 'no-store'
    assert offered.json() == {'resources': 'per_run', 'code_projects': [{'id': a.app, 'name': 'App'},
                              {'id': a.worker, 'name': 'Worker'}], 'collections': None, 'servers': None, 'services': None}
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET resources='fixed' WHERE id=%s", (a.agent,))
    assert a.client.get(f'/api/agents/{a.agent}/run-resources', headers=HEADERS).json() == {'resources': 'fixed'}
    assert a.client.get('/api/agents/missing/run-resources', headers=HEADERS).status_code == 404


def test_a_suggestion_from_a_per_run_chat_drafts_the_whole_configuration(analyst, store):
    a = analyst
    session = events(a.client.post(a.path, headers=HEADERS, json={
        'task': 'Where are the limits?', 'resources': {'code_project_ids': [a.app]}}))[0]['session_id']
    with store.connect() as conn:
        row = conn.execute('SELECT actor_id,config FROM ka.agent_sessions WHERE id=%s', (session,)).fetchone()
        review = conn.execute('''INSERT INTO ka.agent_session_reviews(session_id,actor_id,comment,context,suggestion)
            VALUES (%s,%s,'Shorter','{}','Be shorter.') RETURNING id''', (session, row['actor_id'])).fetchone()['id']
        conn.execute('UPDATE ka.agent_session_reviews SET context=%s WHERE id=%s', (Jsonb({
            'agent_config': row['config'], 'changes': {'system_prompt': None, 'instructions': 'Be brief.',
                                                       'server_instructions': []}}), review))
    source = a.client.get(f'/api/agents/{a.agent}/versions/source', headers=HEADERS, params={'review_id': str(review)}).json()
    assert 'run_resources' not in source['config']
    assert source['config']['code']['all_projects'] is True  # the agent's own selection, not one run's
    assert source['changes']['instructions'] == 'Be brief.'
```

In `services/admin-api/tests/test_agent_resources.py`, delete the test
`test_test_chats_refuse_per_run_agents_until_runs_pass_resources` (a per-run chat without resources now starts with
nothing: covered above).

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_run_resources_test_chats.py -q`
Expected: FAIL: `ImportError: cannot import name 'KEEPS_RESOURCES'`.

- [ ] **Step 3: Implement the test chats**

`services/admin-api/src/knowledge_admin/agent_sessions.py`:
- import `from knowledge_admin.knowledge_agents.run_resources import RunResourceError, RunResources, resolve`;
- after the imports add
  `KEEPS_RESOURCES = 'A conversation keeps the resources it started with. Start a new session to change them.'`;
- `RunInput` and `SessionInput`: add `resources: RunResources | None = None` (`RunInput`: only with a new conversation);
- `create_session`: the insert records the run resources:

```python
    return conn.execute('''INSERT INTO ka.agent_sessions
        (id,project_id,agent,actor_id,title,config,agent_version_id,is_test,run_resources)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *,
        last_activity_at+idle_timeout AS expires_at,config->'config_version' AS config_version''',
        (session_id,project_id,config['id'],actor_id,title,Jsonb(config),
         config.get('config_version',{}).get('id'),is_test,
         Jsonb(config['run_resources']) if 'run_resources' in config else None)).fetchone()
```

- after `create_session` add:

```python
def run_config(store, app_name: str, config: dict[str, Any], resources: RunResources | None) -> dict[str, Any]:
    """The configuration a new conversation pins: a per-run agent's narrowed to the resources it was given."""
    with store.connect() as conn:
        return resolve(conn, store.project(conn, app_name), config, resources)
```

- `start_turn`, in the first `try`: in the `value.session_id is not None` branch, before `config = saved['config']`
  add `if value.resources is not None: raise HTTPException(409, KEEPS_RESOURCES)`; in the `else` branch after
  `config = await asyncio.to_thread(get_agent_config, …)` add
  `config = await asyncio.to_thread(run_config, store, app_name, config, value.resources)`; add
  `except RunResourceError as exc: raise HTTPException(400, str(exc)) from exc` before `except ValueError`;
- `start_turn`, the turn insert records them:

```python
            row = setup.execute('''INSERT INTO ka.operations_runs(id,session_id,project_id,actor_id,agent,task,context,run_resources)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
                (value.id,session_id,project,actor_id,agent_id,redact(value.task,credentials.values()),Jsonb(context),
                 Jsonb(config['run_resources']) if 'run_resources' in config else None)).fetchone()
```

- `create` (`POST /agents/{agent_id}/sessions`): inside `with db.connect() as conn:` compute
  `project = db.project(conn, app_name)` and `config = resolve(conn, project, config, value.resources)` before
  `create_session(…, project_id=project, …)`, and add
  `except RunResourceError as exc: raise HTTPException(400, str(exc)) from exc` before `except LookupError`.

- [ ] **Step 4: Implement the options endpoint and the review draft**

`services/admin-api/src/knowledge_admin/profiles_api.py`: import `per_run` with the other config names and
`from knowledge_admin.knowledge_agents.run_resources import options as run_resource_options`; after the `configuration`
route add:

```python
    @router.get('/{agent_id}/run-resources')
    def run_resources(agent_id: str, response: Response, version_id: UUID | None = None):
        """What a caller may pass to one run of a per-run agent: the Test tab picker and Generate documents."""
        response.headers['Cache-Control'] = 'no-store'
        try:
            config = get_agent_config(agent_id, conninfo=database().conninfo, version_id=version_id)
        except LookupError:
            raise HTTPException(404, 'Agent not found') from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        if not per_run(config):
            return {'resources': 'fixed'}
        with database().connect() as conn:
            return {'resources': 'per_run', **run_resource_options(conn, store.project(conn, app_name), config)}
```

`services/admin-api/src/knowledge_admin/agent_versions.py`, `source`, the review branch: select the session's version
too and draft from the whole configuration when the conversation was per run:

```python
            row = conn.execute('''SELECT r.context,r.suggestion,s.agent_version_id FROM ka.agent_session_reviews r
                JOIN ka.agent_sessions s ON s.id=r.session_id WHERE r.id=%s AND s.agent=%s''', (review_id,agent_id)).fetchone()
            if row is None:
                raise HTTPException(404, 'Suggestion not found for this agent.')
            config = row['context']['agent_config']
            if 'run_resources' in config:
                # A per-run conversation pinned one run's resources: draft from the configuration it ran with,
                # never from the narrowed lists.
                try:
                    config = get_agent_config(agent_id, conninfo=store.conninfo, version_id=row['agent_version_id'])
                except (LookupError, ValueError) as exc:
                    raise HTTPException(409, str(exc)) from None
            changes = row['context'].get('changes', changes)
            suggestion = row['suggestion']
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_run_resources_test_chats.py tests/test_agent_resources.py tests/test_agent_sessions.py tests/test_agent_runs.py tests/test_agent_versions.py tests/test_knowledge_agents_config.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/admin-api/src/knowledge_admin/agent_sessions.py services/admin-api/src/knowledge_admin/profiles_api.py \
  services/admin-api/src/knowledge_admin/agent_versions.py services/admin-api/tests/test_run_resources_test_chats.py \
  services/admin-api/tests/test_agent_resources.py
git commit -m "feat(agent-sessions): test chats of per-run agents run with their run resources

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Generate documents — a Documents choice; per-run agents get the form's sources, fixed agents their own

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/generate_documents.py` (`subjects`, `first_message`)
- Modify: `services/admin-api/src/knowledge_admin/claude_code_api.py` (`SessionInput`, texts, `session_context`,
  `create_session`, `DETAIL`)
- Modify: `services/admin-api/src/knowledge_admin/knowledge_agents/native_sessions.py` (remove `code_roots`)
- Modify: `services/admin-api/tests/test_claude_code_sessions.py`, `services/admin-api/tests/test_native_sessions.py`
- Modify: `services/admin-api/tests/test_agent_resources.py` (delete the interim Task 1 refusal test)
- Create: `services/admin-api/tests/test_generate_run_resources.py`

**Interfaces:**
- Consumes: Task 2 (`resolve`, `RunResources`, `RunResourceError`, fixture `per_run`); Task 3.
- Produces: `SessionInput.collection_ids: list[UUID]` (max 50) on `POST /api/claude-code/sessions`; context snapshot
  key `collections` (`[{'id','name','description'}]`); a `## Documents` section in the first message;
  `claude_code_api.NO_RUN_FOLDER`; the new `NOTHING_CHOSEN`; `agent_sessions_cc.run_resources` written for per-run
  agents; session detail returns `run_resources`; `native_sessions.code_roots` removed.

- [ ] **Step 1: Write the failing tests**

Create `services/admin-api/tests/test_generate_run_resources.py`:

```python
"""Generate documents: per-run agents get exactly the form's sources as their run resources; fixed agents keep their
own configuration and get the sources in the first message (fixed Claude Code agents also as working folders)."""
import json

from knowledge_admin.claude_code_api import NO_RUN_FOLDER
from test_claude_code_agents import claude_code_agent
from test_claude_code_sessions import refused, sessions, start  # noqa: F401  (fixture)
from test_run_resources import per_run  # noqa: F401  (fixture)

NONE_CHOSEN = {'code_project_ids': [], 'collection_ids': [], 'environment_ids': [], 'service_ids': [], 'server_ids': []}


def stored(store, session_id) -> dict:
    with store.connect() as conn:
        return conn.execute('SELECT agent_config,run_resources FROM ka.agent_sessions_cc WHERE id=%s',
                            (session_id,)).fetchone()


def test_a_per_run_native_agent_gets_exactly_the_forms_sources(per_run):
    s = per_run
    session = start(s, **{**NONE_CHOSEN, 'agent_id': s.analyst, 'code_project_ids': [s.repo],
                          'collection_ids': [s.runbooks], 'server_ids': [s.host['id']]})
    row = stored(s.store, session['id'])
    assert row['run_resources'] == {'code_project_ids': [s.repo], 'collection_ids': [s.runbooks],
                                    'server_ids': [s.host['id']], 'service_ids': []}
    pinned = row['agent_config']
    assert pinned['run_resources'] == row['run_resources']
    assert [p['id'] for p in pinned['code']['projects']] == [s.repo] and pinned['code']['all_projects'] is False
    assert [c['name'] for c in pinned['documents']['collections']] == ['runbooks']
    assert [h['id'] for h in pinned['ssh']['servers']] == [s.host['id']] and pinned['services']['items'] == []
    assert s.key['private_key'] not in json.dumps(pinned)  # references only
    assert [f['path'] for f in session['context_snapshot']['folders']] == [f'{s.data}/code/app']
    assert session['run_resources'] == row['run_resources']
    text = session['inputs'][0]['text']
    assert f'## Code\n- Payments API — {s.data}/code/app' in text
    assert '## Documents\n- runbooks: The runbooks documents' in text
    only_code = stored(s.store, start(s, **{**NONE_CHOSEN, 'agent_id': s.analyst, 'code_project_ids': [s.repo]})['id'])
    assert only_code['agent_config']['ssh']['servers'] == []  # no server chosen: none, never all


def test_generate_documents_refuses_sources_a_per_run_agent_may_not_get(per_run):
    s = per_run

    def refusal(**change) -> str:
        return refused(s, **{**NONE_CHOSEN, 'agent_id': s.analyst, **change})
    assert refusal(code_project_ids=[s.worker]) == 'Code project Payments worker is not allowed for this agent.'
    assert refusal(collection_ids=[s.private]) == 'Collection private is not allowed for this agent.'
    assert refusal(server_ids=[s.server['id']]) == 'Server prod-1 is not available: it was removed, archived or disabled.'
    # Production's Payments service brings Payments worker, which the agent may not be given.
    assert refusal(environment_ids=[s.environment]) == 'Code project Payments worker is not allowed for this agent.'
    with s.store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_sessions_cc WHERE agent_id=%s',
                            (s.analyst,)).fetchone()['n'] == 0


def test_a_per_run_claude_code_agent_works_in_exactly_the_run_code_projects(per_run):
    s = per_run
    reader = claude_code_agent(s.client, 'Code reader', model_id=s.claude, resources='per_run',
                               repositories_enabled=True, all_repositories=True,
                               documents_enabled=True, collection_ids=[s.runbooks])
    session = start(s, **{**NONE_CHOSEN, 'agent_id': reader, 'code_project_ids': [s.worker]})
    assert [f['path'] for f in session['context_snapshot']['folders']] == [f'{s.data}/code/worker']  # not every project
    row = stored(s.store, session['id'])
    assert (row['agent_config'], row['run_resources']['code_project_ids']) == (None, [s.worker])
    assert refused(s, **{**NONE_CHOSEN, 'agent_id': reader, 'server_ids': [s.host['id']]}) == 'This agent does not use servers.'
    assert refused(s, **{**NONE_CHOSEN, 'agent_id': reader, 'collection_ids': [s.runbooks]}) == NO_RUN_FOLDER


def test_fixed_agents_keep_their_configuration_and_get_the_sources_in_the_first_message(per_run):
    s = per_run
    session = start(s, collection_ids=[s.private])  # the fixed Claude Code writer, every kind chosen
    assert stored(s.store, session['id'])['run_resources'] is None and session['run_resources'] is None
    assert '## Documents\n- private: The private documents' in session['inputs'][0]['text']
    assert [f['path'] for f in session['context_snapshot']['folders']] == [f'{s.data}/code/app', f'{s.data}/code/worker']
```

In `services/admin-api/tests/test_claude_code_sessions.py`:
- remove `from knowledge_admin.knowledge_agents.native_sessions import code_roots` and the test
  `test_code_roots_add_the_session_folders_and_keep_a_switched_off_agents_projects_out`;
- rename `test_native_agents_start_sessions_with_their_config_and_the_chosen_folders_as_code_roots` to
  `test_native_agents_start_sessions_with_their_own_config` and change two assertions:
  - `assert [f['path'] for f in session['context_snapshot']['folders']] == [app, worker]  # chosen, then its own`
    becomes `assert [f['path'] for f in session['context_snapshot']['folders']] == [worker]  # its own code projects only`;
  - `('Payments worker', [worker]), ('Payments API', [app])]` becomes `('Payments worker', [worker])]` (the form's
    Payments API is in the first message, not a code root).

In `services/admin-api/tests/test_native_sessions.py`:
- the `native` fixture docstring becomes "A native writer with the Claude model and the Payments worker as its own code
  project, and a session that documents the Payments API code project (named in the first message) and the prod-1
  server.";
- in `test_native_turns_write_session_events_the_draft_tokens_and_history`,
  `[p['folder_paths'] for p in code['projects']] == [[f'{data}/code/worker'], [f'{data}/code/app']]` becomes
  `[p['folder_paths'] for p in code['projects']] == [[f'{data}/code/worker']]` (a fixed agent reads its own code
  projects).

In `services/admin-api/tests/test_agent_resources.py`, delete the test
`test_generate_documents_refuses_per_run_agents_until_runs_pass_resources` and its now unused imports (`refused`,
`sessions`).

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_generate_run_resources.py -q`
Expected: FAIL: `ImportError: cannot import name 'NO_RUN_FOLDER'`.

- [ ] **Step 3: Implement the Documents choice**

`services/admin-api/src/knowledge_admin/generate_documents.py`, `subjects`: after the `code` check loop add

```python
    collections = _in_order(conn.execute('''SELECT id,name,description FROM ka.rag_collections
        WHERE project_id=%s AND id=ANY(%s) AND deleted_at IS NULL''', (project, value.collection_ids)).fetchall(),
        value.collection_ids, 'collections')
```

and in the returned dict after `'code_projects': …,` add
`'collections': [{'id': c['id'], 'name': c['name'], 'description': c['description']} for c in collections],`.

`first_message`: after the `## Code` section add

```python
    if context.get('collections'):
        sections.append('## Documents\n' + '\n'.join(
            _line(c['name'], c['description'], []) for c in context['collections']))
```

- [ ] **Step 4: Implement the sessions**

`services/admin-api/src/knowledge_admin/claude_code_api.py`:
- imports: remove `from knowledge_admin.knowledge_agents.native_sessions import code_roots` and the Task 1
  `require_run_resources` import; add `per_run` to the `knowledge_agents.config` import and
  `from knowledge_admin.knowledge_agents.run_resources import RunResourceError, RunResources, resolve`;
- texts: `NOTHING_CHOSEN = 'Choose at least one code project, collection, environment, service or server to document.'`
  and after `NO_FOLDER` add
  `NO_RUN_FOLDER = ('Claude Code needs at least one working folder: choose code projects, or environments or services '
  'with code projects, that this agent accepts.')`;
- `SessionInput`: after `code_project_ids` add
  `collection_ids: list[UUID] = Field(default_factory=list, max_length=50)`; add `'collection_ids'` to the
  de-duplication loop and `self.collection_ids` to the `NOTHING_CHOSEN` check;
- `DETAIL = _select(',s.document_name,s.draft,s.draft_turn,s.prompt_snapshot,s.context_snapshot,s.model_id,s.run_resources')`;
- replace `session_context` with:

```python
def session_context(conn, project: UUID, value: SessionInput, agent_projects: list[dict], *, needs_folder: bool = True,
                    chosen: bool = True, agents_own: bool = True) -> dict:
    """What to document (generate_documents.subjects) plus the folders the agent reads, each folder once. With `chosen`
    (fixed Claude Code agents) the chosen code projects and those of the chosen environments and services come first;
    then `agent_projects`: the agent's own code projects (`agents_own`) or a per-run agent's run code projects. Every
    folder must exist now, inside DATA_ROOT, without a symbolic link on the way. Claude Code needs at least one folder
    (its working directory); native agents do not."""
    context = subjects(conn, project, value)
    candidates = []
    if chosen:
        candidates += [(path, item, False) for item in context['code_projects'] for path in item['paths']]
        candidates += [(path, item, False) for group in ('environments', 'services') for subject in context[group]
                       for item in subject['code_projects'] for path in item['paths']]
    candidates += [(path, {'id': str(item['id']), 'name': item['name']}, agents_own)
                   for item in agent_projects for path in item['folder_paths']]
    folders, seen = [], set()
    for path, item, from_agent in candidates:
        if path in seen:
            continue
        seen.add(path)
        if not Path(path).is_dir():
            raise HTTPException(400, f"The folder {path} of the agent's code project {item['name']} does not exist. "
                                     'Remove the project from the agent or check its folders.' if from_agent else
                                f"The folder {path} of the code project {item['name']} does not exist. "
                                "Check the code project's folders.")
        inside_data_root(path)
        folders.append({'path': path, 'kind': 'code_project', 'name': item['name'], 'code_project_id': item['id']})
    if not folders and needs_folder:
        raise HTTPException(400, NO_FOLDER if agents_own else NO_RUN_FOLDER)
    return {**context, 'folders': folders, 'code_project_ids': [item['id'] for item in context['code_projects']]}
```

- `create_session`: remove the Task 1 `require_run_resources` block; replace the lines from
  `claude_code = agent['runtime'] == 'claude_code'` to the `INSERT INTO ka.agent_sessions_cc(…)` statement with:

```python
            claude_code = agent['runtime'] == 'claude_code'
            given = per_run(config)
            try:  # per-run agents: the form's sources are the run's resources; fixed agents keep their own
                effective = resolve(conn, project, config, RunResources(
                    code_project_ids=value.code_project_ids, collection_ids=value.collection_ids,
                    server_ids=value.server_ids, service_ids=value.service_ids) if given else None,
                    environment_ids=value.environment_ids if given else ())
            except RunResourceError as exc:
                raise HTTPException(400, str(exc)) from None
            readable = effective['code']['projects'] if effective['code']['enabled'] else []
            context = session_context(conn, project, value, readable, needs_folder=claude_code,
                                      chosen=claude_code and not given, agents_own=not given)
            snapshot = {'system_prompt': agent_system_prompt(config), 'agent_version': config.get('config_version')}
            if claude_code:
                snapshot['claude_code_tools'] = config['claude_code_tools']
            # Native turns run with this config (credential references only); the worker decrypts per turn.
            pinned = None if claude_code else Jsonb(jsonable_encoder(effective))
            session_id = uuid4()
            conn.execute("""INSERT INTO ka.agent_sessions_cc(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,
                context_snapshot,document_name,folder,runtime,agent_config,run_resources)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (session_id, project, agent['id'], actor, model['id'], value.document_name, Jsonb(jsonable_encoder(snapshot)),
                 Jsonb(context), value.document_name, value.folder, agent['runtime'], pinned,
                 Jsonb(effective['run_resources']) if given else None))
```

`services/admin-api/src/knowledge_admin/knowledge_agents/native_sessions.py`: delete `code_roots` and the `copy`
import if nothing else uses it; the module docstring's first sentence becomes "Native agents in Generate documents
sessions: a turn's SDK lifecycle as the session's run events, and agent-session-worker, …" (the pinned configuration
is built by `claude_code_api.create_session`).

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_generate_run_resources.py tests/test_claude_code_sessions.py tests/test_generate_documents.py tests/test_native_sessions.py tests/test_claude_code_runner.py tests/test_claude_code_worker.py tests/test_agent_resources.py -q`
Expected: PASS.

Run: `git grep -n "code_roots" -- services/admin-api`
Expected: no matches.

- [ ] **Step 6: Commit**

```bash
git add services/admin-api/src/knowledge_admin services/admin-api/tests/test_generate_run_resources.py \
  services/admin-api/tests/test_claude_code_sessions.py services/admin-api/tests/test_native_sessions.py \
  services/admin-api/tests/test_agent_resources.py
git commit -m "feat(generate-documents): Documents choice; per-run agents get the form's sources as run resources

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: admin-ui — the Test tab "Resources for this run" picker

**Files:**
- Modify: `services/admin-ui/app/lib/profiles.ts`, `services/admin-ui/app/lib/operations.ts`
- Modify: `services/admin-ui/app/components/AgentTestView.tsx`
- Modify: `services/admin-ui/app/components/__tests__/agentSessions.test.tsx`

**Interfaces:**
- Consumes: Task 4 (`GET /api/agents/{id}/run-resources`, `resources` on `POST /api/agents/{id}/runs`, the pinned
  `config.run_resources` and the narrowed lists in the session's `config`).
- Produces (`lib/profiles.ts`): `RunResourceItem`, `RunResourceKind`, `RunResourceOptions`, `RunResourcesInput`,
  `NO_RUN_RESOURCES`, `RUN_RESOURCE_KINDS`, `getRunResourceOptions(id, versionId?)`; (`lib/operations.ts`)
  `startOperationsRun(task, agentId, signal, onEvent, sessionId?, versionId?, resources?)`.

- [ ] **Step 1: Write the failing tests**

`services/admin-ui/app/components/__tests__/agentSessions.test.tsx`:
- replace `vi.mock('../../lib/profiles');` with
  `vi.mock('../../lib/profiles', async () => ({ ...await vi.importActual('../../lib/profiles'), getAgentConfig: vi.fn(), getRunResourceOptions: vi.fn() }));`
  (the picker's kind list is a constant of that module) and import `getRunResourceOptions` and
  `type RunResourceOptions` next to `getAgentConfig`;
- in `beforeEach` add `vi.mocked(getRunResourceOptions).mockResolvedValue({ resources: 'fixed' });`;
- append:

```tsx
const ANALYST: RunResourceOptions = { resources: 'per_run', collections: null, servers: [], services: null,
  code_projects: [{ id: 'repo', name: 'Payments API' }, { id: 'worker', name: 'Payments worker' }] };
const ONLY_REPO = { code_project_ids: ['repo'], collection_ids: [], server_ids: [], service_ids: [] };

it('a per-run agent picks the resources for the run and shows them in the configuration', async () => {
  vi.mocked(getRunResourceOptions).mockResolvedValue(structuredClone(ANALYST));
  renderPage();
  const picker = await screen.findByRole('region', { name: 'Resources for this run' });
  expect(within(picker).queryByText('Collections')).toBeNull();  // a kind the agent does not use
  expect(within(picker).getByText('None allowed. Choose them in the agent’s tabs.')).toBeVisible();  // servers
  fireEvent.click(within(picker).getByRole('checkbox', { name: 'Payments API' }));
  fireEvent.click(screen.getByRole('button', { name: 'Show config' }));
  const shown = await screen.findByLabelText('Agent configuration');
  expect(JSON.parse(shown.textContent!).run_resources).toEqual(ONLY_REPO);
});

it('the chosen resources start the conversation once and stay with it', async () => {
  vi.mocked(getRunResourceOptions).mockResolvedValue(structuredClone(ANALYST));
  const saved = structuredClone(conversation); saved.runs = [];
  saved.config = { id: 'custom', code: { projects: [{ id: 'repo', name: 'Payments API' }] }, run_resources: ONLY_REPO };
  vi.mocked(api.getAgentSession).mockImplementation(async () => structuredClone(saved));
  vi.mocked(api.startOperationsRun).mockImplementation(async (task, _agentId, _signal, onEvent) => {
    const result = { ...run, id: `run-${saved.runs.length + 1}`, task, answer: `Answer ${saved.runs.length + 1}`,
      context: { ...run.context, config: saved.config } };
    onEvent({ event: 'started', data: { ...result, status: 'running', answer: '' } });
    saved.runs.push(result);
    onEvent({ event: 'completed', data: result });
  });
  renderPage();
  fireEvent.click(within(await screen.findByRole('region', { name: 'Resources for this run' })).getByRole('checkbox', { name: 'Payments API' }));
  fireEvent.change(screen.getByLabelText('Task'), { target: { value: 'Where are the limits?' } });
  fireEvent.click(screen.getByRole('button', { name: 'Run task' }));
  expect(await screen.findByText('Answer 1')).toBeVisible();
  expect(vi.mocked(api.startOperationsRun).mock.calls[0][6]).toEqual(ONLY_REPO);
  expect(screen.queryByRole('region', { name: 'Resources for this run' })).toBeNull();  // fixed for the conversation
  expect(screen.getByLabelText('Resources for this run')).toHaveTextContent('Resources for this run: Payments API');
  fireEvent.change(screen.getByLabelText('Task'), { target: { value: 'And the callers?' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
  expect(await screen.findByText('Answer 2')).toBeVisible();
  expect(vi.mocked(api.startOperationsRun).mock.calls[1][6]).toBeUndefined();  // follow-ups never pass resources
});
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-ui && pnpm exec vitest run app/components/__tests__/agentSessions.test.tsx`
Expected: FAIL: `getRunResourceOptions` is not exported / no region `Resources for this run`.

- [ ] **Step 3: Implement**

`services/admin-ui/app/lib/profiles.ts`, after `RESOURCES_HELP`:

```ts
export interface RunResourceItem { id: string; name: string }
export type RunResourceKind = 'code_projects' | 'collections' | 'servers' | 'services';
/** What a caller may pass to one run of a per-run agent; null: a kind the agent does not use. */
export type RunResourceOptions = { resources: AgentResources } & Partial<Record<RunResourceKind, RunResourceItem[] | null>>;
export interface RunResourcesInput { code_project_ids: string[]; collection_ids: string[]; server_ids: string[]; service_ids: string[] }
export const NO_RUN_RESOURCES: RunResourcesInput = { code_project_ids: [], collection_ids: [], server_ids: [], service_ids: [] };
export const RUN_RESOURCE_KINDS: [RunResourceKind, keyof RunResourcesInput, string][] = [
  ['code_projects', 'code_project_ids', 'Code projects'], ['collections', 'collection_ids', 'Collections'],
  ['servers', 'server_ids', 'Servers'], ['services', 'service_ids', 'Services']];
```

and after `getAgentOptions`:

```ts
export const getRunResourceOptions = (id: string, versionId?: string) =>
  request<RunResourceOptions>(path(id) + '/run-resources' + (versionId ? `?version_id=${encodeURIComponent(versionId)}` : ''));
```

`services/admin-ui/app/lib/operations.ts`: `import type { RunResourcesInput } from './profiles';`;
`startOperationsRun` gets a last parameter `resources?: RunResourcesInput` and its body becomes
`{ id: crypto.randomUUID(), task, session_id: sessionId, version_id: versionId, is_test: true, ...(resources ? { resources } : {}) }`.

`services/admin-ui/app/components/AgentTestView.tsx`:
- imports: `import { getAgentConfig, getRunResourceOptions, NO_RUN_RESOURCES, RUN_RESOURCE_KINDS, type RunResourceOptions, type RunResourcesInput } from '../lib/profiles';`,
  `import { Checkbox } from './ui/checkbox';` and `FieldLegend, FieldSet` added to the `./ui/field` import;
- above the component:

```tsx
/** The names of what a per-run conversation was given, from its pinned configuration. */
function runSummary(config: Record<string, unknown>) {
  const listed = (section: string, list: string) =>
    ((config[section] as Record<string, unknown> | undefined)?.[list] as { name: string }[] | undefined) ?? [];
  const names = [...listed('code', 'projects'), ...listed('documents', 'collections'), ...listed('ssh', 'servers'),
    ...listed('services', 'items')].map(item => item.name);
  return names.length ? names.join(', ') : 'nothing';
}
```

- state, after `const [configError, setConfigError] = useState('');`:

```tsx
  const [runOptions, setRunOptions] = useState<RunResourceOptions | null>(null);
  const [runResources, setRunResources] = useState<RunResourcesInput>(NO_RUN_RESOURCES);
  const perRun = runOptions?.resources === 'per_run';
```

- after the `useEffect(() => { void refreshHistory()… }, [agentId]);` line:

```tsx
  useEffect(() => {
    let live = true; setRunOptions(null);
    getRunResourceOptions(agentId, versionId ?? undefined).then(value => { if (live) setRunOptions(value); })
      .catch(reason => { if (live) setError(reason instanceof Error ? reason.message : 'Could not load the resources for this run.'); });
    return () => { live = false; };
  }, [agentId, versionId, revision]);
```

- `selectSession`: add `setRunResources(NO_RUN_RESOURCES);` to its first line;
- `send`: the last arguments of `startOperationsRun` become
  `sessionId ?? undefined, sessionId ? undefined : versionId ?? undefined, !sessionId && perRun ? runResources : undefined);`;
- before `{sessionId && !conversation && !error && <p role="status" …>Loading conversation…</p>}` add:

```tsx
    {perRun && !sessionId && <section aria-label="Resources for this run" className="flex flex-col gap-3 rounded-lg border p-3">
      <div><p className="text-sm font-medium">Resources for this run</p>
        <p className="text-xs text-muted-foreground">This agent works only with what you choose here. The choice is fixed for the conversation, like its configuration.</p></div>
      {RUN_RESOURCE_KINDS.map(([option, key, legend]) => {
        const items = runOptions?.[option];
        if (!items) return null;  // a kind the agent does not use
        return <FieldSet key={key}><FieldLegend variant="label">{legend}</FieldLegend>
          {items.length === 0 && <FieldDescription>None allowed. Choose them in the agent’s tabs.</FieldDescription>}
          {items.map(item => <Field key={item.id} orientation="horizontal">
            <Checkbox id={`run-${key}-${item.id}`} disabled={busy || streaming} checked={runResources[key].includes(item.id)}
              onCheckedChange={checked => setRunResources(current => ({ ...current,
                [key]: checked ? [...current[key], item.id] : current[key].filter(id => id !== item.id) }))} />
            <FieldLabel htmlFor={`run-${key}-${item.id}`}>{item.name}</FieldLabel></Field>)}
        </FieldSet>;
      })}
    </section>}
    {perRun && conversation && <p aria-label="Resources for this run" className="text-xs text-muted-foreground">Resources for this run: {runSummary(conversation.config)}</p>}
```

- the configuration preview: `JSON.stringify({prompt:task,config},null,2)` becomes
  `JSON.stringify(conversation || !perRun ? {prompt:task,config} : {prompt:task,config,run_resources:runResources},null,2)`
  (a conversation's pinned `config` already holds its `run_resources`).

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd services/admin-ui && pnpm exec tsc --noEmit && pnpm exec vitest run app/components/__tests__/agentSessions.test.tsx app/components/__tests__/agentTabs.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-ui/app/lib/profiles.ts services/admin-ui/app/lib/operations.ts \
  services/admin-ui/app/components/AgentTestView.tsx services/admin-ui/app/components/__tests__/agentSessions.test.tsx
git commit -m "feat(admin-ui): Test tab picks the resources of a per-run agent's conversation

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: admin-ui — Generate documents: Documents, and what a per-run agent accepts

**Files:**
- Modify: `services/admin-ui/app/lib/claudeCode.ts` (`SessionInput`, `SessionDetail`)
- Modify: `services/admin-ui/app/components/ClaudeCodeSessionForm.tsx`
- Modify: `services/admin-ui/app/components/__tests__/claudeCodeSessionForm.test.tsx`

**Interfaces:**
- Consumes: Task 1 (`AgentSummary.resources`), Task 4 (`getRunResourceOptions`, Task 6's types), Task 5
  (`collection_ids`, per-run refusals).
- Produces: `SessionInput.collection_ids: string[]`; `SessionDetail.run_resources?: RunResourcesInput | null`.

- [ ] **Step 1: Write the failing tests**

`services/admin-ui/app/components/__tests__/claudeCodeSessionForm.test.tsx`:
- in `bodies['/api/agents'].agents` append
  `{ id: 'analyst', name: 'Code analyst', runtime: 'openai', resources: 'per_run', enabled: true, is_coordinator: false, model_id: 'gpt', model_name: 'GPT' }`
  and
  `{ id: 'reader', name: 'Code reader', runtime: 'claude_code', resources: 'per_run', enabled: true, is_coordinator: false, model_id: 'claude', model_name: 'Claude on Bedrock' }`;
- add to `bodies`:

```ts
  '/api/rag/collections?status=false': { collections: [{ id: 'runbooks', name: 'runbooks' }] },
  '/api/agents/analyst/run-resources': { resources: 'per_run', code_projects: [{ id: 'repo', name: 'Payments API' }],
    collections: null, servers: [{ id: 'srv', name: 'prod-1' }], services: null },
  '/api/agents/reader/run-resources': { resources: 'per_run', code_projects: [{ id: 'worker', name: 'Payments worker' }],
    collections: [{ id: 'runbooks', name: 'runbooks' }], servers: null, services: null },
```

- every `expect(posts).toEqual([{ … }])` gets `collection_ids: []` after `code_project_ids`;
- append:

```tsx
test('a per-run agent is offered only what it accepts and receives exactly the choice', async () => {
  const posts = setup();
  await select('Agent', 'Code analyst (Native)');
  expect(await screen.findByText('This agent does not take collections.')).toBeVisible();
  expect(screen.getByText(/^This agent works only with what you choose here/)).toBeVisible();
  expect(screen.getByRole('checkbox', { name: 'Documents' })).toBeDisabled();
  expect(screen.getByRole('checkbox', { name: 'Services' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Document name'), { target: { value: 'Payments' } });
  check('Code');
  expect(screen.getByRole('checkbox', { name: 'Payments API' })).toBeVisible();
  expect(screen.queryByRole('checkbox', { name: 'Payments worker' })).toBeNull();  // not allowed for this agent
  check('Payments API');
  check('Servers'); check('prod-1 (10.0.0.5)');
  fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
  expect(await screen.findByText('Session page')).toBeVisible();
  expect(posts).toEqual([{ agent_id: 'analyst', code_project_ids: ['repo'], collection_ids: [], environment_ids: [], service_ids: [],
    server_ids: ['srv'], instructions: '', document_name: 'Payments', folder: 'payments-api-docs' }]);
});

test('a per-run Claude Code agent says which sources it can use now', async () => {
  setup();
  await select('Agent', 'Code reader (Claude Code)');
  expect(await screen.findByText('This agent does not take servers.')).toBeVisible();
  expect(screen.getByText(/collections, servers and services are described in the first message until Claude Code gets the agent tools/)).toBeVisible();
  expect(screen.getByRole('checkbox', { name: 'Documents' })).toBeEnabled();
});

test('Documents adds collections to the first message of a fixed agent', async () => {
  const posts = setup();
  await select('Agent', 'Technical writer (Claude Code)');
  expect(screen.getByText(/^This agent works with its own resources/)).toBeVisible();
  fireEvent.change(screen.getByLabelText('Document name'), { target: { value: 'Runbooks' } });
  check('Documents'); check('runbooks');
  fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
  expect(await screen.findByText('Session page')).toBeVisible();
  expect(posts).toEqual([{ agent_id: 'writer', code_project_ids: [], collection_ids: ['runbooks'], environment_ids: [], service_ids: [],
    server_ids: [], instructions: '', document_name: 'Runbooks', folder: 'docs' }]);
});
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-ui && pnpm exec vitest run app/components/__tests__/claudeCodeSessionForm.test.tsx`
Expected: FAIL: no `Documents` checkbox, posts without `collection_ids`.

- [ ] **Step 3: Implement**

`services/admin-ui/app/lib/claudeCode.ts`: `SessionInput` gets `collection_ids: string[];` after `code_project_ids`;
`SessionDetail` gets `run_resources?: RunResourcesInput | null;` (import the type from `./profiles`).

`services/admin-ui/app/components/ClaudeCodeSessionForm.tsx`:
- imports: `getRunResourceOptions`, `type RunResourceKind`, `type RunResourceOptions` from `~/lib/profiles` and
  `import { getCollectionNames, type CollectionSummary } from '~/lib/rag';` (RAG being unavailable leaves
  **Documents** empty instead of blocking the form);
- replace the `Kind` type, `KINDS` and `NOTHING` with:

```tsx
type Kind = 'code' | 'documents' | 'environments' | 'services' | 'servers';
type Item = { id: string; name: string };
type Data = { agents: AgentSummary[]; items: Record<Kind, Item[]>; folders: string[]; noModel: string };
const KINDS: { kind: Kind; label: string; legend: string }[] = [
  { kind: 'code', label: 'Code', legend: 'Code projects' }, { kind: 'documents', label: 'Documents', legend: 'Collections' },
  { kind: 'environments', label: 'Environment', legend: 'Environments' },
  { kind: 'services', label: 'Services', legend: 'Services' }, { kind: 'servers', label: 'Servers', legend: 'Servers' }];
const NOTHING: Record<Kind, string[]> = { code: [], documents: [], environments: [], services: [], servers: [] };
/** The run resource kind a choice passes to a per-run agent; an environment brings its services and servers. */
const RUN_KIND: Partial<Record<Kind, RunResourceKind>> = { code: 'code_projects', documents: 'collections', services: 'services', servers: 'servers' };
const NOTES = {
  fixed: 'This agent works with its own resources. What you choose is described in its first message.',
  fixedClaudeCode: 'This agent works with its own resources. What you choose is described in its first message; the code projects, and those of the environments and services you choose, also become its read-only working folders.',
  perRun: 'This agent works only with what you choose here, from what it accepts. Environments and services also bring their code projects and servers.',
  perRunClaudeCode: 'This agent works only with what you choose here, from what it accepts. Claude Code opens the code projects as read-only working folders; collections, servers and services are described in the first message until Claude Code gets the agent tools (next update).',
};
const sourcesNote = (agent?: AgentSummary) => !agent ? 'Choose an agent to see how it uses what you choose here.'
  : agent.resources === 'per_run' ? (agent.runtime === 'claude_code' ? NOTES.perRunClaudeCode : NOTES.perRun)
  : agent.runtime === 'claude_code' ? NOTES.fixedClaudeCode : NOTES.fixed;
```

- state: add `const [runOptions, setRunOptions] = useState<RunResourceOptions | null>(null);`;
- the loading `Promise.all`: add `getCollectionNames().catch(() => ({ collections: [] as CollectionSummary[] }))` after
  `getCodeProjects()`, receive it as `collections`, and add
  `documents: collections.collections.map(item => ({ id: item.id, name: item.name })),` to `items`;
- replace `const picked = …` (and move `const agent = …` above it) with:

```tsx
  const agent = data?.agents.find(item => item.id === agentId);
  const perRun = agent?.resources === 'per_run';
  useEffect(() => {
    setRunOptions(null);
    if (!agent || agent.resources !== 'per_run') return;
    let live = true;
    getRunResourceOptions(agent.id).then(value => { if (live) setRunOptions(value); })
      .catch(e => { if (live) setError(e instanceof Error ? e.message : 'Could not load what this agent accepts'); });
    return () => { live = false; };
  }, [agent?.id, agent?.resources]);
  /** What a choice offers: every item for a fixed agent; for a per-run agent only what it accepts (null: a kind it
   * does not take). */
  function offered(kind: Kind): Item[] | null {
    const items = data?.items[kind] ?? [];
    const runKind = RUN_KIND[kind];
    if (!perRun || !runKind) return items;
    const allowed = runOptions?.[runKind];
    if (allowed === undefined) return [];  // still loading
    return allowed === null ? null : items.filter(item => allowed.some(option => option.id === item.id));
  }
  const picked = (kind: Kind) => {
    const items = offered(kind);
    return kinds.includes(kind) && items ? chosen[kind].filter(id => items.some(item => item.id === id)) : [];
  };
```

- `ready`: `Boolean(agent?.model_id && (!perRun || runOptions) && KINDS.some(({ kind }) => picked(kind).length > 0) && documentSlug(name) && target && !badFolder)`;
- `submit`: add `collection_ids: picked('documents'),` after `code_project_ids: picked('code'),`;
- `check` gets a last parameter `disabled = false` passed to `<Checkbox id={id} disabled={disabled} …>`;
- replace the `What to document` `FieldSet` with:

```tsx
      <FieldSet><FieldLegend>What to document</FieldLegend>
        <FieldDescription>{sourcesNote(agent)}</FieldDescription>
        {KINDS.map(({ kind, label, legend }) => { const items = offered(kind); return <div key={kind} className="flex flex-col gap-2">
          {check(`what-${kind}`, label, kinds.includes(kind) && items !== null, value => toggleKind(kind, value), items === null)}
          {items === null && <FieldDescription className="ml-6">This agent does not take {legend.toLowerCase()}.</FieldDescription>}
          {kinds.includes(kind) && items !== null && <FieldSet className="ml-6"><FieldLegend variant="label">{legend}</FieldLegend>
            {items.length === 0 && <FieldDescription>Nothing to choose yet.</FieldDescription>}
            {items.map(item => check(`generate-${kind}-${item.id}`, item.name, chosen[kind].includes(item.id), value => toggle(kind, item.id, value)))}
          </FieldSet>}
        </div>; })}
      </FieldSet>
```

  (The `useEffect` sits above the `if (!data) return …` early return, like every hook.)

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd services/admin-ui && pnpm exec tsc --noEmit && pnpm exec vitest run app/components/__tests__/claudeCodeSessionForm.test.tsx app/components/__tests__/claudeCodeSession.test.tsx app/components/__tests__/claudeCodeSessions.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-ui/app/lib/claudeCode.ts services/admin-ui/app/components/ClaudeCodeSessionForm.tsx \
  services/admin-ui/app/components/__tests__/claudeCodeSessionForm.test.tsx
git commit -m "feat(admin-ui): Generate documents offers Documents and what a per-run agent accepts

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: What a per-run agent accepts — the All agents text and the capability tab notes

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/profiles_api.py` (`listing`)
- Modify: `services/admin-api/tests/test_agent_resources.py`
- Modify: `services/admin-ui/app/lib/profiles.ts`, `services/admin-ui/app/components/AgentsView.tsx`,
  `services/admin-ui/app/components/AgentDetailView.tsx`
- Modify: `services/admin-ui/app/components/__tests__/agentTypes.test.tsx`

**Interfaces:**
- Consumes: Task 1 (`resources` in the list, the version overlay).
- Produces: `GET /api/agents` rows carry `accepts: [{'kind': 'code'|'documents'|'servers'|'services', 'limit': int | 'any'}]`
  (per-run agents: the kinds switched on, in that order, with their selection size or `any`; fixed agents: `[]`), from
  the active version when there is one. UI: `ResourceLimit`, `AgentSummary.accepts?`, `resourcesSummary(agent)`,
  `PER_RUN_NOTES`.

- [ ] **Step 1: Write the failing tests**

Append to `services/admin-api/tests/test_agent_resources.py`:

```python
def test_the_list_says_what_a_per_run_agent_accepts(store, saved_agent):
    client = saved_agent[0]
    assert listed(client)['operations']['accepts'] == []  # fixed
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET resources='per_run' WHERE id='operations'")
    assert listed(client)['operations']['accepts'] == [
        {'kind': 'code', 'limit': 1}, {'kind': 'documents', 'limit': 1}, {'kind': 'servers', 'limit': 1},
        {'kind': 'services', 'limit': 1}]
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET all_repositories=true,ssh_enabled=false WHERE id='operations'")
        conn.execute("DELETE FROM ka.agent_repositories WHERE agent_id='operations'")
    expected = [{'kind': 'code', 'limit': 'any'}, {'kind': 'documents', 'limit': 1}, {'kind': 'services', 'limit': 1}]
    assert listed(client)['operations']['accepts'] == expected
    new_version(client, 'Code, documents and services')  # activates "Initial configuration": the active version decides
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET documents_enabled=false,services_enabled=false WHERE id='operations'")
    assert listed(client)['operations']['accepts'] == expected
```

`services/admin-ui/app/components/__tests__/agentTypes.test.tsx`:
- in `All agents shows how each agent gets its resources`, `within(row).getByText('Per run')` becomes
  `within(row).getByText('Per run · nothing')`;
- append:

```tsx
test('the list says what a per-run agent accepts', async () => {
  vi.mocked(profiles.getAgents).mockResolvedValue({ agents: [
    { ...writer, resources: 'per_run', accepts: [{ kind: 'code', limit: 'any' }, { kind: 'servers', limit: 2 }],
      model_name: 'Claude on Bedrock', prompt_title: 'Built-in documentation writer', prompt_version: 1 }] });
  render(<MemoryRouter initialEntries={['/agents']}><Routes><Route path="/agents" element={<AgentsView />} /></Routes></MemoryRouter>);
  const row = (await screen.findByRole('link', { name: 'Technical writer' })).closest('tr')!;
  expect(within(row).getByText('Per run · code (any) · servers (2)')).toBeVisible();
});

test('capability tabs of a per-run agent say what callers may pass', async () => {
  vi.mocked(profiles.getAgent).mockImplementation(async id => ({ ...structuredClone(writer), id, runtime: 'openai', model_id: 'gpt', resources: 'per_run' }));
  const user = userEvent.setup();
  open('/agents/search?view=git');
  expect(await screen.findByText('Per run: callers may pass any of the code projects selected here. All code projects = any code project.')).toBeVisible();
  await user.click(screen.getByRole('tab', { name: 'SSH' }));
  expect(screen.getByText(/^Per run: callers may pass any of the servers selected here/)).toBeVisible();
  await user.click(screen.getByRole('tab', { name: 'Skills' }));
  expect(screen.queryByText(/^Per run:/)).toBeNull();
});
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_agent_resources.py -q`
Expected: FAIL: `KeyError: 'accepts'`.

Run: `cd services/admin-ui && pnpm exec vitest run app/components/__tests__/agentTypes.test.tsx`
Expected: FAIL: no text `Per run · code (any) · servers (2)`.

- [ ] **Step 3: Implement**

`services/admin-api/src/knowledge_admin/profiles_api.py`, `listing`: add to the `SELECT` after
`saved.config AS version_config,saved.version AS config_version,`:

```sql
                (SELECT count(*) FROM ka.agent_repositories x JOIN ka.repositories r ON r.id=x.repository_id
                    WHERE x.agent_id=a.id AND r.archived_at IS NULL)::int AS code_count,
                (SELECT count(*) FROM ka.agent_collections x JOIN ka.rag_collections c ON c.id=x.collection_id
                    WHERE x.agent_id=a.id AND c.deleted_at IS NULL)::int AS collection_count,
                (SELECT count(*) FROM ka.operations_servers x WHERE x.agent=a.id)::int AS server_count,
                (SELECT count(*) FROM ka.agent_services x WHERE x.agent_id=a.id)::int AS service_count,
```

and in the loop, before `config = row.pop('version_config')`:

```python
                code_count, collection_count = row.pop('code_count'), row.pop('collection_count')
                limits = {'code': 'any' if row['all_repositories'] else code_count,
                          'documents': 'any' if row['all_collections'] else collection_count,
                          'servers': row.pop('server_count'), 'services': row.pop('service_count')}
```

inside `if config is not None:` (after the existing overlay) add:

```python
                    limits = {'code': 'any' if config['code'].get('all_projects') else len(config['code']['projects']),
                              'documents': 'any' if config['documents'].get('all_collections')
                              else len(config['documents'].get('collections', [])),
                              'servers': len(config['ssh']['servers']), 'services': len(config['services']['items'])}
```

and after the `if` block:

```python
                enabled = {'code': row['repositories_enabled'], 'documents': row['documents_enabled'],
                           'servers': row['ssh_enabled'], 'services': row['services_enabled']}
                row['accepts'] = ([{'kind': kind, 'limit': limit} for kind, limit in limits.items() if enabled[kind]]
                                  if row['resources'] == 'per_run' else [])
```

`services/admin-ui/app/lib/profiles.ts`:

```ts
export interface ResourceLimit { kind: 'code' | 'documents' | 'servers' | 'services'; limit: number | 'any' }
/** "Fixed", or what a per-run agent accepts: "Per run · code (any) · servers (2)". */
export const resourcesSummary = (agent: { resources?: AgentResources; accepts?: ResourceLimit[] }) =>
  agent.resources !== 'per_run' ? 'Fixed'
    : agent.accepts?.length ? ['Per run', ...agent.accepts.map(({ kind, limit }) => `${kind} (${limit})`)].join(' · ') : 'Per run · nothing';
/** Notes on the capability tabs of a per-run agent: its selections are what callers may pass. */
export const PER_RUN_NOTES: Partial<Record<string, string>> = {
  git: 'Per run: callers may pass any of the code projects selected here. All code projects = any code project.',
  documents: 'Per run: callers may pass any of the collections selected here. All collections = any collection.',
  ssh: 'Per run: callers may pass any of the servers selected here. Their instructions and connection overrides still apply.',
  services: 'Per run: callers may pass any of the services selected here.',
};
```

and `AgentSummary` gets `accepts?: ResourceLimit[]`.

`services/admin-ui/app/components/AgentsView.tsx`: the Resources cell and the search text use
`resourcesSummary(a)` instead of `resourcesLabel(a.resources)` (import accordingly).

`services/admin-ui/app/components/AgentDetailView.tsx`: import `PER_RUN_NOTES` from `../lib/profiles` and after the
`{claudeCode && claudeCodeNotes[view] && …}` line add
`{profile.resources === 'per_run' && PER_RUN_NOTES[view] && <Alert><AlertDescription>{PER_RUN_NOTES[view]}</AlertDescription></Alert>}`.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_agent_resources.py tests/test_profiles.py tests/test_knowledge_agents_config.py -q`
Expected: PASS.

Run: `cd services/admin-ui && pnpm exec tsc --noEmit && pnpm exec vitest run app/components/__tests__/agentTypes.test.tsx app/components/__tests__/agentTabs.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/profiles_api.py services/admin-api/tests/test_agent_resources.py \
  services/admin-ui/app/lib/profiles.ts services/admin-ui/app/components/AgentsView.tsx \
  services/admin-ui/app/components/AgentDetailView.tsx services/admin-ui/app/components/__tests__/agentTypes.test.tsx
git commit -m "feat(agents): the list and the capability tabs say what a per-run agent accepts

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Docs and full verification

**Files:**
- Modify: `docs/user-guide.md`, `docs/agent-profiles.md`, `docs/documentation.md`, `docs/agents.md`

- [ ] **Step 1: Update the docs**

`docs/user-guide.md`:
- **§4.11 Agents:** the list sentence becomes "`/agents` lists agents with **Agent type**, **Resources**, capabilities,
  model, active system prompt and status. **Resources** shows `Fixed`, or what a per-run agent accepts, for example
  `Per run · code (any) · servers (2)`."; the wizard paragraph: "Its first two fields are **Agent type** (Native or
  Claude Code) and **Resources** (Fixed or Per run). The review shows them as one line (for example `Native · Per
  run`); to change them go back with **Edit role**. For Claude Code, the generator may only propose a Claude model …"
  (the rest unchanged; drop "The review **General** tab shows the type again and you can still change it");
- the **General** row of the tab table adds "**Resources** (Fixed: the agent always works with its selections; Per
  run: each run says what to work on, from those selections; the coordinator is always Fixed)"; the **Documents**,
  **Code projects**, **SSH** and **Services** rows add "For a per-run agent: what callers may pass (the tab says so)";
  the **Test** row: "Native: chat with the agent (a per-run agent first picks **Resources for this run**)";
- after **Test tab.** add a paragraph **Per-run agents (role agents).** "A per-run agent defines what it does (prompt,
  instructions, skills, tools); each run says what it works on. Its tabs' selections are what a run **may** be given:
  **All code projects** / **All collections** = any. On the **Test** tab, **Resources for this run** offers one list
  per kind the agent uses; the choice is fixed for the conversation and shown in **Show config**. The agent's tools
  reach exactly what the run was given: a kind with nothing chosen answers "No code project was given for this run"
  (never everything). A choice outside the selection is refused (`Code project <name> is not allowed for this
  agent.`), as are removed, archived or disabled resources and kinds the agent does not use. Resources passed to a
  fixed agent are refused (`This agent uses fixed resources.`). Per-run agents cannot join the multi-agent board yet.
  Example: a **Code analyst** with Code projects on and **All code projects**, tested with `payments-api` in one
  conversation and `helpers` in the next.";
- **§4.13 Generate documents, The form:** "**What to document**: tick **Code**, **Documents**, **Environment**,
  **Services** and/or **Servers** …"; after the form list add **Fixed and per-run agents**: "A fixed agent works with
  its own resources: what you choose is described in its first message, and for Claude Code agents the chosen code
  projects (and those of the chosen environments and services) also become read-only working folders. A per-run agent
  receives exactly what you choose as its run resources: the form offers only what it accepts (a kind it does not
  take is disabled), environments and services also bring their code projects and the servers they are deployed on
  (servers without SSH are skipped), and a resource it may not be given refuses the session with its name. A per-run
  Claude Code agent opens the chosen code projects as working folders; collections, servers and services are described
  in the first message until Claude Code gets the agent tools.";
- **What the agent receives:** the example gets a `## Documents` section (`- runbooks: Payments runbooks`) after
  `## Code`;
- **Working folders:** "(Claude Code) fixed agents: the chosen code projects, then the code projects linked to the
  chosen environments and services, then the agent's own; per-run agents: the run's code projects";
- **Native agents:** "A fixed native agent reads its own code projects (its Code projects tab), and the form's sources
  are in its first message; a per-run native agent reads exactly the run's code projects and collections and reaches
  exactly the run's servers. Its tools then work as in its Test tab. …" (replacing "The chosen and linked code
  projects' folders and the agent's own code projects become its code roots");
- **Errors:** "Choose at least one code project, collection, environment, service or server to document."; add the
  per-run refusals (`… is not allowed for this agent.`, `… is not available: it was removed, archived or disabled.`,
  `This agent does not use <kind>.`) and `Claude Code needs at least one working folder: choose code projects, or
  environments or services with code projects, that this agent accepts.`;
- **§5 B. Document a service from its source code:** add "With a per-run Code analyst, choose the code project in the
  form: the agent reads that project only.";
- **Appendix A:** under the table add "For a per-run agent every tool works on the run's resources only; a kind with
  nothing given is refused with `No … was given for this run`."

`docs/agent-profiles.md`:
- the tab table's General row adds "Resources (Fixed / Per run)"; after the capabilities paragraph add "**Resources.**
  A fixed agent always uses its selections. A per-run agent's selections are what a run may be given (All = any);
  each run passes `code_project_ids`, `collection_ids`, `server_ids` and `service_ids`, validated against them and
  narrowed into the run's configuration (`knowledge_agents/run_resources.py`); skills and MCP stay fixed. The
  coordinator is always fixed.";
- **New agent:** "The wizard asks for **Agent type** and **Resources** first (page 1) and shows them as one line on the
  review. `POST /api/agents/wizard` accepts `runtime`, `resources` (default `fixed`) and `claude_code_tools`.";
- **Persistence and APIs**, after the 0074 paragraph: "Migration `0075_agent_resources` adds `agents.resources`
  (`fixed` | `per_run`, default `fixed`) and `per_run_agent_is_not_the_coordinator`; its down migration refuses while
  per-run agents or per-run configuration versions exist. Migration `0076_run_resources` adds `run_resources` to
  `agent_sessions_cc`, `agent_sessions` and `operations_runs`; its down migration drops only these records.";
- API list: "`PUT /api/agents/{id}` also accepts `resources` (the coordinator: 400 `The coordinator uses fixed
  resources.`)"; "`GET /api/agents` returns `resources` and `accepts` (from the active version)"; "`GET
  /api/agents/{id}/run-resources?version_id=` returns what a run may be given per kind (`null`: a kind the agent does
  not use), or `{resources: 'fixed'}`"; "`POST /api/agents/{id}/runs` and `POST /api/agents/{id}/sessions` accept
  `resources` with a new conversation only (follow-ups: 409); sessions and runs return `run_resources`".

`docs/documentation.md`, **Generate documents**: the snapshot paragraph replaces "with the working folders as code
roots via `native_sessions.code_roots`" by "a fixed agent's own configuration, or a per-run agent's configuration
narrowed to the form's sources (`run_resources.resolve`)"; add `collection_ids` to the request and `collections` to the
context snapshot; add `run_resources` (per-run agents) to the stored session; the refusals list gets the per-run texts
and `NO_RUN_FOLDER`.

`docs/agents.md`, **Configuration versions**: add "The snapshot holds `resources`; versions saved before 0075 are
fixed. A suggestion from a per-run test chat drafts from the configuration the conversation ran with, never from its
narrowed lists."

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

Run: `git grep -n "code_roots\|chosen folders as code roots" -- services docs ':!docs/superpowers'`
Expected: no matches.

Run: `git grep -n "Choose at least one code project, environment, service or server" -- services docs ':!docs/superpowers'`
Expected: no matches.

Run: `git grep -n "require_run_resources" -- services/admin-api/src`
Expected: `knowledge_agents/config.py` (definition), `agents.py` (`create_agent`), `agent_sessions.py`
(`create_session`, `start_turn`) and `tools/__init__.py` (`require_given`) only.

- [ ] **Step 4: Commit**

```bash
git add docs
git commit -m "docs: per-run resources (role agents) in the guides

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

## Out of scope (say so in the PR)

- **Built-in role agents** installed at start-up (Code analyst, SSH agent, Docs agent) and their update notice.
- **A per-run model** (an allowed-models list chosen per run).
- **Agents calling agents** (the builder) passing their own run resources to role agents; the board refuses per-run
  agents until then.
- **The tool gateway (knowledge agents step 2):** per-run Claude Code agents use only their run's code projects as
  working folders; their other run resources are recorded and described in the first message.
- **Code analyst question sets** and their evaluation runs; the documentation object.

## Follow-ups

- Let the board pass run resources to per-run specialists (with the builder).
- Show a session's run resources on the Generate documents session page (the detail already returns them).
- Offer per-run agents' accepted kinds in the wizard's design context so the designer can propose role agents.
