# Agent experts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A saved native agent can be offered as an expert that every other native agent (and every Generate documents agent) calls as its own tool `ask_<slug>`; Generate documents agents also get `run_subagent` and guidance to split multi-part documents.

**Architecture:** `ka.agents.expert` marks an expert; a caller's `expert:<id>` row in `ka.agent_tools` (mode `off`) hides one expert from it. When a session is created, the experts it may call are pinned into its configuration (`config['experts']`). At run time `create_agent` turns each pinned entry into an SDK `FunctionTool` whose call goes to the turn's `Subagents` host, which loads the expert's current configuration and credentials through a loader the session runner provides, and runs it like a subagent (same slots, per-turn limit, Stop, recording with `parent_call_id`). The UI groups expert events like subagent events.

**Tech Stack:** Python 3.12, FastAPI, psycopg 3, OpenAI Agents SDK 0.19.2, pytest; React Router 7, shadcn/ui, Vitest; golang-migrate SQL migrations.

**Spec:** `docs/superpowers/specs/2026-10-09-agent-experts-design.md`

## Global Constraints

- Migration name: `0085_agent_experts` (0084 is `0084_remove_claude_code`).
- Expert rows in `ka.agent_tools`: `tool = 'expert:<agent id>'`, only `mode='off'` is stored, `subagent_allowed=false`; no row = on.
- Tool name: `ask_<slug>` — name lowercased, every run of characters other than `a-z0-9` replaced by `_`, trimmed of `_`, at most 50 characters (then trimmed of `_` again); empty slug → `expert`; a slug already taken by an earlier entry (experts ordered by `lower(name), id`) gets `_2`, `_3`, ….
- Tool description: `Ask <name>, an expert agent. <When to use>` + per-run: ` It works only on the resources you pass (it accepts: <kinds>).` / fixed: ` It uses its own resources.` Kinds in this order and wording: `code projects`, `document collections`, `servers`, `services`.
- Messages, verbatim: `Experts need When to use.` · `Expert {name} is not available.` · `Expert {name} failed: {message}` · `This turn already started 16 helpers.` · `{label} {id} is not one of your resources.` (existing `NOT_GIVEN`) · `Unknown expert: {tool}.`
- Per turn: at most 16 subagents and expert calls together (`MAX_PER_TURN`); at most `AGENT_MAX_SUBAGENTS` (default 4) running at once, shared.
- An expert's task: non-empty, at most 20,000 characters (same as subagents, same messages).
- UI copy, verbatim: switch label `Offer as an expert`; help `Other agents can call this agent as a tool. Its When to use text tells them when.`; Tools tab group label `Experts`; empty `No experts yet. Turn on Offer as an expert on an agent's General tab.`; on an expert agent `When this agent runs as an expert it cannot call experts.`; badge `Expert`; Agent runs label `Called as expert by <caller>`.
- Never run `make` targets; never touch `.env` or `.local/`; credentials are never logged, stored or streamed unredacted; never `git stash`; never touch the running Docker stack's database; work only in this worktree; commit trailer `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`; never `git add .superpowers`.
- Test commands (from the worktree root): admin-api `cd services/admin-api && uv run --frozen pytest -q <files>`; admin-ui `cd services/admin-ui && npx vitest run <files>` and `npx tsc --noEmit`; database `bash db/tests/run.sh`.

## Review Focus

1. **An expert switched off, disabled or deleted after a session pinned it** → the call is a tool error `Expert <name> is not available.`, the caller continues. (Task 4 test `test_an_expert_that_is_no_longer_available_is_a_tool_error`.)
2. **A per-run expert given a resource the caller does not have** (the model invents an id) → tool error naming it; nothing is decrypted. (Task 4 test `test_a_resource_the_caller_lacks_is_refused_before_anything_is_loaded`.)
3. **A fixed expert's secrets** (its SSH key, its model key) appearing in its answer or events → redacted in the tool result and in the caller's record. (Task 4 test `test_a_fixed_expert_uses_its_own_credentials_and_they_are_redacted`.)
4. **Two experts whose names slug to the same tool name** (`Payments API` / `payments-api`) → distinct tools `ask_payments_api`, `ask_payments_api_2`; neither shadows the other. (Task 3 test `test_tool_names_are_slugs_and_collisions_get_a_number`.)
5. **Stop while an expert runs** → the expert's run is cancelled with the turn, its credentials are cleared, no further events. (Task 4 test `test_stop_cancels_a_running_expert_and_clears_its_credentials`.)

---

### Task 1: Migration 0085 and the database checks

**Files:**
- Create: `db/migrations/0085_agent_experts.up.sql`, `db/migrations/0085_agent_experts.down.sql`
- Modify: `db/tests/run.sh` (the down-step block after `run_migrate postgres down 1` near line 58, and a new PASS check after the 0083 check)
- Test: `services/admin-api/tests/test_agent_experts_migration.py`

**Interfaces:**
- Produces: column `ka.agents.expert boolean NOT NULL DEFAULT false`; check `subagents_do_not_spawn` now `CHECK (NOT subagent_allowed OR (tool <> 'run_subagent' AND tool NOT LIKE 'expert:%'))`.

- [ ] **Step 1: Write the failing test**

```python
"""0085: agents can be offered as experts; a caller's expert:<id> rows are never given to subagents. Down removes the
column and the rows and restores the old check."""
from pathlib import Path

import psycopg
import pytest

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0085_agent_experts.up.sql', '0085_agent_experts.down.sql'


def run(conn, name: str) -> None:
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def an_agent(conn, name: str) -> str:
    return conn.execute("""INSERT INTO ka.agents(id,name,description,when_to_use,enabled,system_prompt)
        VALUES (gen_random_uuid()::text,%s,'','',true,'Prompt') RETURNING id""", (name,)).fetchone()['id']


def test_expert_column_defaults_off_and_expert_rows_are_never_for_subagents(store):
    with store.connect() as conn:
        caller, expert = an_agent(conn, 'Caller'), an_agent(conn, 'Payments')
        assert conn.execute('SELECT expert FROM ka.agents WHERE id=%s', (expert,)).fetchone()['expert'] is False
        conn.execute("INSERT INTO ka.agent_tools(agent_id,tool,mode,subagent_allowed) VALUES (%s,%s,'off',false)",
                     (caller, f'expert:{expert}'))
    with pytest.raises(psycopg.errors.CheckViolation), store.connect() as conn:
        conn.execute("INSERT INTO ka.agent_tools(agent_id,tool,mode,subagent_allowed) VALUES (%s,%s,'off',true)",
                     (caller, f'expert:{caller}'))


def test_down_removes_the_column_and_the_rows_and_up_restores_the_column(store):
    with store.connect() as conn:
        caller, expert = an_agent(conn, 'Caller'), an_agent(conn, 'Payments')
        conn.execute('UPDATE ka.agents SET expert=true WHERE id=%s', (expert,))
        conn.execute("INSERT INTO ka.agent_tools(agent_id,tool,mode,subagent_allowed) VALUES (%s,%s,'off',false)",
                     (caller, f'expert:{expert}'))
        run(conn, DOWN)
        assert conn.execute("""SELECT count(*) AS n FROM information_schema.columns WHERE table_schema='ka'
            AND table_name='agents' AND column_name='expert'""").fetchone()['n'] == 0
        assert conn.execute("SELECT count(*) AS n FROM ka.agent_tools WHERE tool LIKE 'expert:%'").fetchone()['n'] == 0
        run(conn, UP)
        assert conn.execute('SELECT expert FROM ka.agents WHERE id=%s', (expert,)).fetchone()['expert'] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/admin-api && uv run --frozen pytest -q tests/test_agent_experts_migration.py`
Expected: FAIL (column `expert` does not exist / migration file missing).

- [ ] **Step 3: Write the migrations**

`db/migrations/0085_agent_experts.up.sql`:

```sql
BEGIN;
SET search_path = ka, public;

-- An expert is a saved agent other agents call as a tool (ask_<name>); its When to use text is the tool's description.
ALTER TABLE agents ADD COLUMN expert boolean NOT NULL DEFAULT false;
COMMENT ON COLUMN agents.expert IS 'Offered as an expert: every other agent may call it as a tool unless it switched it off.';

-- A caller's choice for one expert: 'expert:<agent id>' with mode off (no row: on). Experts are never given to
-- subagents; the unused ask_agent:<id> form is replaced.
ALTER TABLE agent_tools DROP CONSTRAINT subagents_do_not_spawn;
ALTER TABLE agent_tools ADD CONSTRAINT subagents_do_not_spawn
    CHECK (NOT subagent_allowed OR (tool <> 'run_subagent' AND tool NOT LIKE 'expert:%'));
COMMENT ON COLUMN agent_tools.tool IS 'A catalog name (read_file), mcp:<mcp_server_id>:<tool name> or expert:<agent id>.';

COMMIT;
```

`db/migrations/0085_agent_experts.down.sql`:

```sql
BEGIN;
SET search_path = ka, public;

DELETE FROM agent_tools WHERE tool LIKE 'expert:%';
ALTER TABLE agent_tools DROP CONSTRAINT subagents_do_not_spawn;
ALTER TABLE agent_tools ADD CONSTRAINT subagents_do_not_spawn
    CHECK (NOT subagent_allowed OR (tool <> 'run_subagent' AND tool NOT LIKE 'ask_agent:%'));
COMMENT ON COLUMN agent_tools.tool IS NULL;
ALTER TABLE agents DROP COLUMN expert;

COMMIT;
```

- [ ] **Step 4: Update `db/tests/run.sh`**

The first rollback after `up` now lands on 84. Replace

```bash
run_migrate postgres down 1 >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
rolled_back_version=$(docker exec "$test_container" psql -X -U postgres -Atc \
    'SELECT version FROM public.golang_schema_migrations WHERE NOT dirty')
[ "$rolled_back_version" = 83 ]
```

with

```bash
run_migrate postgres down 1 >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
experts_removed=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT NOT EXISTS (SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'ka' AND table_name = 'agents' AND column_name = 'expert')")
[ "$experts_removed" = t ] || { echo 'FAIL: 0085 down left agents.expert' >&2; exit 1; }
run_migrate postgres down 1 >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
rolled_back_version=$(docker exec "$test_container" psql -X -U postgres -Atc \
    'SELECT version FROM public.golang_schema_migrations WHERE NOT dirty')
[ "$rolled_back_version" = 83 ]
```

and after the 0083 `session_notices` PASS line add:

```bash
# 0085 offers agents as experts; a caller's expert:<id> rows are never given to subagents.
experts=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT EXISTS (SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'ka' AND table_name = 'agents' AND column_name = 'expert')
        AND pg_get_constraintdef((SELECT oid FROM pg_constraint WHERE conname = 'subagents_do_not_spawn')) LIKE '%expert:%'")
[ "$experts" = t ] || { echo 'FAIL: 0085 expert column or check missing' >&2; exit 1; }
printf '%s\n' 'PASS: agents can be experts; expert rows are never given to subagents.'
```

(If the script contains other places that assume the newest version is 84, e.g. a second `down 1` sequence, apply the same one-extra-step change there.)

- [ ] **Step 5: Run the tests**

Run: `cd services/admin-api && uv run --frozen pytest -q tests/test_agent_experts_migration.py tests/test_agent_tools_migration.py` → PASS.
Run: `bash db/tests/run.sh` → every check PASS (it uses its own throwaway container).

- [ ] **Step 6: Commit**

```bash
git add db/migrations/0085_agent_experts.up.sql db/migrations/0085_agent_experts.down.sql db/tests/run.sh services/admin-api/tests/test_agent_experts_migration.py
git commit -m "feat(db): 0085 agents can be offered as experts; expert rows are never given to subagents"
```

---

### Task 2: The expert switch, delete, clone and Apply (admin-api)

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/profiles_api.py` (`ProfileInput`, `update`, `delete`)
- Modify: `services/admin-api/src/knowledge_admin/agent_clones.py` (`copy_configuration`: skip a copied row naming the target itself)
- Test: `services/admin-api/tests/test_agent_experts.py` (new)

**Interfaces:**
- Consumes: column `ka.agents.expert` (Task 1).
- Produces: profile GET/PUT and list rows carry `expert: bool` (GET already returns `SELECT *`); `ProfileInput.expert: bool | None` (None keeps it); constant `EXPERT_NEEDS_WHEN = 'Experts need When to use.'` in `profiles_api.py`.

- [ ] **Step 1: Write the failing tests**

```python
"""Offer as an expert: the switch on the agent, its rules, and what delete, clone and Apply to original do with it."""
from knowledge_admin.profiles_api import EXPERT_NEEDS_WHEN, ProfileInput
from test_agent_tools_api import put, view
from test_agents import agent_mounts, saved_agent  # noqa: F401  (fixtures)
from test_profiles import create, update
from test_repositories import HEADERS


def profile(client, agent_id):
    return client.get(f'/api/agents/{agent_id}', headers=HEADERS).json()


def test_the_switch_round_trips_needs_when_to_use_and_a_client_that_omits_it_keeps_it(store, saved_agent):
    client = saved_agent[0]
    agent = create(client, 'Payments')
    assert agent['expert'] is False
    refused = update(client, agent, expert=True, when_to_use='  ')
    assert refused.status_code == 400 and refused.json()['error']['message'] == EXPERT_NEEDS_WHEN
    on = update(client, agent, expert=True, when_to_use='Questions about payments.')
    assert on.status_code == 200, on.text
    assert on.json()['expert'] is True and on.json()['revision'] == agent['revision'] + 1
    body = {field: on.json()[field] for field in ProfileInput.model_fields if field != 'expert'}
    kept = client.put(f"/api/agents/{agent['id']}", headers=HEADERS, json={**body, 'name': 'Payments 2'})
    assert kept.status_code == 200 and kept.json()['expert'] is True
    cleared = update(client, kept.json(), when_to_use='')
    assert cleared.status_code == 400  # an expert keeps its When to use
    listed = {row['id']: row for row in client.get('/api/agents', headers=HEADERS).json()['agents']}
    assert listed[agent['id']]['expert'] is True


def test_deleting_an_expert_deletes_every_callers_row_for_it(store, saved_agent):
    client = saved_agent[0]
    caller, expert = create(client, 'Caller'), create(client, 'Payments')
    expert = update(client, expert, expert=True, when_to_use='Payments.').json()
    with store.connect() as conn:
        conn.execute("INSERT INTO ka.agent_tools(agent_id,tool,mode,subagent_allowed) VALUES (%s,%s,'off',false)",
                     (caller['id'], f"expert:{expert['id']}"))
    deleted = client.request('DELETE', f"/api/agents/{expert['id']}", headers=HEADERS,
                             json={'revision': expert['revision'], 'confirmed': True})
    assert deleted.status_code == 204, deleted.text
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM ka.agent_tools WHERE tool=%s",
                            (f"expert:{expert['id']}",)).fetchone()['n'] == 0


def test_a_clone_copies_the_callers_choices_and_starts_as_no_expert(store, saved_agent):
    client = saved_agent[0]
    other = update(client, create(client, 'Payments'), expert=True, when_to_use='Payments.').json()
    lead = update(client, create(client, 'Lead'), expert=True, when_to_use='Leads.').json()
    with store.connect() as conn:
        conn.execute("INSERT INTO ka.agent_tools(agent_id,tool,mode,subagent_allowed) VALUES (%s,%s,'off',false)",
                     (lead['id'], f"expert:{other['id']}"))
    clone = client.post(f"/api/agents/{lead['id']}/clone", headers=HEADERS)
    assert clone.status_code == 201, clone.text
    copy = profile(client, clone.json()['id'])
    assert copy['expert'] is False
    with store.connect() as conn:
        rows = conn.execute('SELECT tool,mode FROM ka.agent_tools WHERE agent_id=%s AND tool LIKE %s',
                            (copy['id'], 'expert:%')).fetchall()
    assert rows == [{'tool': f"expert:{other['id']}", 'mode': 'off'}]
```

Add one test for Apply to original, following the existing Apply tests in `tests/test_agent_clones.py` (read how they call `GET/POST /api/agents/{id}/apply` with `revision` and `fingerprint`): the clone switches off the original (`expert:<original id>` row on the clone, since the original is an expert); after Apply, the original's `expert` is unchanged (`True`) and the original has no `expert:<original id>` row.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/admin-api && uv run --frozen pytest -q tests/test_agent_experts.py`
Expected: FAIL (`EXPERT_NEEDS_WHEN` import error).

- [ ] **Step 3: Implement**

In `profiles_api.py`:

```python
EXPERT_NEEDS_WHEN = 'Experts need When to use.'
```

`ProfileInput` gains, after `subagents`:

```python
    expert: bool | None = Field(default=None, strict=True)  # Offer as an expert; None keeps the switch
```

In `update`, after the revision check:

```python
                expert = old['expert'] if value.expert is None else value.expert
                if expert and not value.when_to_use.strip():
                    raise HTTPException(400, EXPERT_NEEDS_WHEN)
```

and add `expert=%s` to the `UPDATE ka.agents SET …` statement (with `expert` in the parameters, before `revision=revision+1`). Add `**({'expert': expert} if expert != old['expert'] else {})` to the audit `switch` details.

In `delete`, before `DELETE FROM ka.agents WHERE id=%s`:

```python
            conn.execute('DELETE FROM ka.agent_tools WHERE tool=%s', (f'expert:{agent_id}',))
```

In `agent_clones.copy_configuration`, where `agent_tools` rows are copied, skip the row naming the target itself (Apply to original copies the clone's rows onto the original):

```python
        if row['tool'] == f'expert:{target}':
            continue  # an agent never calls itself
```

(Use the function's actual parameter name for the target agent id. `expert` is not in `CONFIGURATION`, so clone and Apply leave it as it is: a new clone is created with the column default `false`.)

- [ ] **Step 4: Run tests**

Run: `cd services/admin-api && uv run --frozen pytest -q tests/test_agent_experts.py tests/test_profiles.py tests/test_agent_clones.py tests/test_subagent_switch.py` → PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/profiles_api.py services/admin-api/src/knowledge_admin/agent_clones.py services/admin-api/tests/test_agent_experts.py
git commit -m "feat(admin-api): Offer as an expert on the agent profile; delete, clone and Apply handle expert rows"
```

---

### Task 3: Offered experts, tool names, pinning and the Tools tab API

**Files:**
- Create: `services/admin-api/src/knowledge_admin/knowledge_agents/experts.py`
- Modify: `services/admin-api/src/knowledge_admin/tools_api.py` (GET lists experts; PUT accepts `expert:<id>`)
- Modify: `services/admin-api/src/knowledge_admin/agent_sessions.py` (`create`: pin experts)
- Modify: `services/admin-api/src/knowledge_admin/knowledge_agents/ephemeral.py` (allow `run_subagent`; pin all experts)
- Modify: `services/admin-api/src/knowledge_admin/generations_api.py` (pass the connection's experts into `ad_hoc_config` if the signature needs it — see Step 3)
- Modify: `services/admin-api/src/knowledge_admin/knowledge_agents/prompts/subagent.md`
- Test: `services/admin-api/tests/test_expert_tools.py` (new)

**Interfaces:**
- Consumes: `ka.agents.expert`, `expert:<id>` rows (Tasks 1–2).
- Produces, in `knowledge_agents/experts.py`:
  - `ROW_PREFIX = 'expert:'`
  - `def slug(name: str) -> str`
  - `def offered(conn, caller_id: str | None) -> list[dict]` — experts this caller may call, ordered by `lower(name), id`; each `{'id', 'name', 'when_to_use', 'resources': 'fixed'|'per_run', 'accepts': list[str], 'tool': str, 'description': str}` (`accepts`: kind keys from `run_resources.KINDS` the expert uses, in KINDS order; empty for fixed).
  - `def pin(conn, config: dict, caller_id: str | None) -> dict` — returns `{**config, 'experts': offered(conn, caller_id)}`.
  - Every pinned entry is what Task 4 reads (`entry['id']`, `entry['name']`, `entry['tool']`, `entry['description']`, `entry['resources']`).
- Tools tab rows for experts: `{'tool': 'expert:<id>', 'label': name, 'summary': when_to_use, 'group': 'experts', 'mode': 'on'|'off', 'usage': None, 'default_usage': '', 'subagent_allowed': False, 'available': True, 'reason': None, 'tab': None, 'resources': 'fixed'|'per_run'}`; plus the response gains `'expert': bool` (whether this agent is itself an expert).

- [ ] **Step 1: Write the failing tests**

```python
"""Experts as tools: which experts a caller is offered, their tool names and descriptions, pinning into Test and
Generate documents sessions, and the Tools tab's Experts group."""
from knowledge_admin.knowledge_agents.experts import offered, slug
from test_agent_tools_api import put, view
from test_agents import agent_mounts, saved_agent  # noqa: F401  (fixtures)
from test_profiles import create, update
from test_repositories import HEADERS


def expert(client, name, when='When it fits.', **changes):
    return update(client, create(client, name), expert=True, when_to_use=when, **changes).json()


def test_tool_names_are_slugs_and_collisions_get_a_number(store, saved_agent):
    assert slug('Payments API') == 'payments_api'
    assert slug('  Ünïcode — only!! ') == 'n_code_only'
    assert slug('***') == ''
    assert len(slug('x' * 80)) == 50
    client = saved_agent[0]
    model = saved_agent[1]  # read test_agents.saved_agent for which element is the model id; adjust the index
    first = expert(client, 'Payments API', model_id=model)
    second = expert(client, 'payments-api', model_id=model)
    with store.connect() as conn:
        tools = {entry['id']: entry['tool'] for entry in offered(conn, None)}
    assert tools[first['id']] == 'ask_payments_api' and tools[second['id']] == 'ask_payments_api_2'


def test_only_enabled_experts_with_a_model_other_than_the_caller_and_not_switched_off_are_offered(store, saved_agent):
    ...  # create: an expert without a model, a disabled expert, a plain agent, the caller itself as an expert, and an
    # expert the caller switched off (expert:<id> row mode off); offered(conn, caller) returns only the remaining one,
    # with description 'Ask <name>, an expert agent. <when>' + ' It uses its own resources.' for a fixed expert, and for
    # a per-run expert with code projects and services on: ' It works only on the resources you pass (it accepts:
    # code projects, services).'


def test_a_test_session_pins_the_offered_experts(store, saved_agent):
    ...  # POST /api/agents/{caller}/sessions; the session's agent_config['experts'] lists the offered experts


def test_generate_documents_pins_every_expert_and_offers_run_subagent(store, saved_agent):
    ...  # POST /api/generations/sessions (see tests/test_generations.py for the payload); agent_config['experts']
    # lists every offered expert; agent_config['tools'] has run_subagent on with subagent_allowed false


def test_the_tools_tab_lists_other_experts_on_by_default_and_saves_off_rows(store, saved_agent):
    ...  # view(client, caller) has group 'experts' rows for every other expert (mode on, resources), never itself,
    # and 'expert' False; put(...) with {'tool': 'expert:<id>', 'mode': 'off'} stores the off row (subagent_allowed
    # false); putting it back 'on' deletes the row; an unknown 'expert:nope' or the caller's own id → 400
    # 'Unknown expert: expert:nope.'
```

Write the four `...` tests in full (they follow `test_subagent_switch.py` and `test_generations.py`; every assertion listed in the comment must be in the test).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/admin-api && uv run --frozen pytest -q tests/test_expert_tools.py`
Expected: FAIL (module `experts` missing).

- [ ] **Step 3: Implement**

`knowledge_agents/experts.py`:

```python
"""Experts: saved agents other agents call as tools (ask_<name>). Which experts a caller is offered, their tool names
and descriptions, and pinning them into a session's configuration. Running a call is subagents.Subagents.ask."""
import re
from typing import Any

from knowledge_admin.knowledge_agents.config import model_fits
from knowledge_admin.knowledge_agents.run_resources import KINDS

ROW_PREFIX = 'expert:'
MAX_SLUG = 50
# The agents row switch behind each run-resource kind, in KINDS order.
SWITCHES = {'code_project_ids': 'repositories_enabled', 'collection_ids': 'documents_enabled',
            'server_ids': 'ssh_enabled', 'service_ids': 'services_enabled'}
OFFERED = '''SELECT a.id,a.name,a.when_to_use,a.resources,a.repositories_enabled,a.documents_enabled,a.ssh_enabled,
    a.services_enabled,m.provider,m.api_format,m.model
    FROM ka.agents a JOIN ka.llm_models m ON m.id=a.model_id AND m.enabled
    WHERE a.expert AND a.enabled AND a.id IS DISTINCT FROM %(caller)s
      AND NOT EXISTS (SELECT 1 FROM ka.agent_tools t WHERE t.agent_id=%(caller)s AND t.tool='expert:'||a.id
                      AND t.mode='off')
    ORDER BY lower(a.name),a.id'''


def slug(name: str) -> str:
    """'Payments API' -> 'payments_api': lowercase a-z0-9 words joined by _, at most 50 characters."""
    return re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')[:MAX_SLUG].strip('_')


def description(name: str, when_to_use: str, resources: str, accepts: list[str]) -> str:
    text = f'Ask {name}, an expert agent. {when_to_use.strip()}'
    if resources == 'per_run':
        kinds = ', '.join(kind.plural for kind in KINDS if kind.key in accepts)  # Kind's plural label
        return f'{text} It works only on the resources you pass (it accepts: {kinds}).'
    return f'{text} It uses its own resources.'


def offered(conn, caller_id: str | None) -> list[dict[str, Any]]:
    """The experts this caller may call: experts, enabled, with an enabled agent model, never the caller itself, and not
    switched off on its Tools tab. A Generate documents agent (caller None) gets them all."""
    entries, taken = [], set()
    for row in conn.execute(OFFERED, {'caller': caller_id}):
        if not model_fits(row):
            continue
        base = f"ask_{slug(row['name']) or 'expert'}"
        tool, number = base, 1
        while tool in taken:
            number += 1
            tool = f'{base}_{number}'
        taken.add(tool)
        accepts = [kind.key for kind in KINDS if row[SWITCHES[kind.key]]] if row['resources'] == 'per_run' else []
        entries.append({'id': row['id'], 'name': row['name'], 'when_to_use': row['when_to_use'],
                        'resources': row['resources'], 'accepts': accepts, 'tool': tool,
                        'description': description(row['name'], row['when_to_use'], row['resources'], accepts)})
    return entries


def pin(conn, config: dict[str, Any], caller_id: str | None) -> dict[str, Any]:
    """The configuration a session pins, with the experts it may call (later changes apply to new sessions)."""
    return {**config, 'experts': offered(conn, caller_id)}
```

(Check `run_resources.Kind`'s field names — it is `Kind('code_project_ids', 'code', 'Code project', 'code projects', 'code_projects')`; use the field holding `'code projects'` / `'document collections'` / `'servers'` / `'services'`.)

`agent_sessions.create`: after `pinned = resolve(...)` and its checks, `pinned = experts.pin(conn, pinned, agent_id)` before the INSERT.

`ephemeral.py`: `NOT_FOR_AD_HOC = frozenset()` is removed; `base_config` builds tools for every catalog name with `run_subagent` given `subagent_allowed: False`:

```python
        'tools': tool_items({'tool': name, 'mode': 'on', 'usage': None, 'subagent_allowed': name != SUBAGENT}
                            for name in CATALOG),
```

and `ad_hoc_config` returns `pin(conn, config, None)`. Update the module docstring line that says run_subagent is excluded.

`tools_api.agent_tools`: after the MCP rows, list experts. Use `offered`'s ordering and naming but **including** ones the caller switched off (the tab shows the switch), so query separately:

```python
EXPERTS = '''SELECT a.id,a.name,a.when_to_use,a.resources FROM ka.agents a
    WHERE a.expert AND a.id<>%s ORDER BY lower(a.name),a.id'''
...
    for found in conn.execute(EXPERTS, (agent_id,)):
        name = f"expert:{found['id']}"
        row = saved.get(name)
        tools.append({'tool': name, 'label': found['name'], 'summary': found['when_to_use'], 'group': 'experts',
                      'mode': row['mode'] if row else 'on', 'usage': None, 'default_usage': '',
                      'subagent_allowed': False, 'available': True, 'reason': None, 'tab': None,
                      'resources': found['resources']})
    return {'revision': agent['revision'], 'expert': agent['expert'], 'tools': tools}
```

(add `expert` to the `AGENT` select). `save`: before the MCP validation, accept `expert:` names:

```python
            experts = {row['id'] for row in conn.execute('SELECT id FROM ka.agents WHERE expert AND id<>%s', (agent_id,))}
            for choice in value.tools:
                if choice.tool.startswith('expert:') and choice.tool.removeprefix('expert:') not in experts:
                    raise HTTPException(400, UNKNOWN_EXPERT.format(tool=choice.tool))
```

with `UNKNOWN_EXPERT = 'Unknown expert: {tool}.'`, skip expert names in the MCP loop, and when inserting: an expert choice with mode `on` stores nothing; mode `off` inserts `(agent_id, tool, 'off', None, False)`. Expert rows of agents that are not (or no longer) experts are kept when absent from the PUT? No: PUT replaces all rows (existing behaviour); keep rows for current non-expert ids by re-inserting the saved `expert:` rows whose id is not in `experts` (so a caller's choice survives an expert being switched off and on again):

```python
            kept = [row for row in conn.execute("SELECT tool FROM ka.agent_tools WHERE agent_id=%s AND tool LIKE 'expert:%%'",
                                                (agent_id,)) if row['tool'].removeprefix('expert:') not in experts]
            # … after the DELETE:
            for row in kept:
                conn.execute(INSERT_TOOL, (agent_id, row['tool'], 'off', None, False))
```

`prompts/subagent.md`: append the sentence `When asked for a section, return it as finished markdown with its evidence cited.` before the final sentence `Finish with a concise answer for the agent that started you.`

- [ ] **Step 4: Run tests**

Run: `cd services/admin-api && uv run --frozen pytest -q tests/test_expert_tools.py tests/test_agent_tools_api.py tests/test_subagent_switch.py tests/test_generations.py tests/test_subagent_config.py tests/test_tool_catalog.py` → PASS. Fix any existing test that asserted `run_subagent` absent from ad-hoc configurations (it is now present by design; update its assertion and name).

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/knowledge_agents/experts.py services/admin-api/src/knowledge_admin/tools_api.py services/admin-api/src/knowledge_admin/agent_sessions.py services/admin-api/src/knowledge_admin/knowledge_agents/ephemeral.py services/admin-api/src/knowledge_admin/knowledge_agents/prompts/subagent.md services/admin-api/tests/test_expert_tools.py
git commit -m "feat(admin-api): experts offered to callers, pinned into sessions and listed on the Tools tab; Generate documents gets run_subagent"
```

---

### Task 4: Calling an expert at run time

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/tools/sdk.py` (add `expert_tool`)
- Modify: `services/admin-api/src/knowledge_admin/agents.py` (`create_agent` offers expert tools)
- Modify: `services/admin-api/src/knowledge_admin/knowledge_agents/subagents.py` (`Subagents.ask`, `load_expert`, shared limit, `subagent_config` drops experts)
- Modify: `services/admin-api/src/knowledge_admin/knowledge_agents/experts.py` (add `load`)
- Modify: `services/admin-api/src/knowledge_admin/knowledge_agents/native_sessions.py` (`NativeRunner` passes the loader)
- Test: `services/admin-api/tests/test_expert_calls.py` (new); adjust `tests/test_subagents.py` for the new limit message

**Interfaces:**
- Consumes: pinned `config['experts']` entries (Task 3).
- Produces:
  - `subagents.LIMIT = 'This turn already started 16 helpers.'` (renamed text; the constant name stays).
  - `experts.UNAVAILABLE = 'Expert {name} is not available.'`, `experts.FAILED = 'Expert {name} failed: {message}'`.
  - `experts.load(store, project_id, entry: dict, resources: Mapping[str, list[str]] | None) -> tuple[dict, dict[str, str]]` — sync (runs in a thread): the expert's run configuration and decrypted credentials, or `ValueError` with a message the model sees.
  - `Subagents.__init__(…, load_expert: Callable[[dict, Mapping | None], tuple[dict, dict]] | None = None)` — called as `load_expert(entry, given_resources)` in a thread.
  - `async Subagents.ask(context, entry, task, resources, call_id) -> str`.
  - `tools.sdk.expert_tool(entry: dict) -> FunctionTool`.
  - Recorded first event of an expert call: `('input', {'task': task, 'expert': {'id': entry['id'], 'name': entry['name']}, 'parent_call_id': call_id})`; every expert event row has `agent_id` = the expert's id (see Step 3, `SubagentEvents.add`).

- [ ] **Step 1: Write the failing tests**

Build on `tests/test_subagents.py`'s `host`, `FakeSubagent`, `Recorder`, `llm`, `message`, `tool`, `call` helpers (import them from there). Tests:

```python
"""Calling an expert: checks, the expert's own configuration and credentials, the shared limits, Stop, recording."""
import asyncio

import pytest

from knowledge_admin.capabilities.context import TOOL_CALL_ID
from knowledge_admin.knowledge_agents.experts import FAILED, UNAVAILABLE
from knowledge_admin.knowledge_agents.subagents import LIMIT, MAX_PER_TURN, Subagents
from test_subagents import CREDENTIALS, OFFERED, MCP, SCRATCH, FakeSubagent, Recorder, host, llm, message, parent
# (import whichever of these test_subagents defines; FakeSubagent/Recorder may live in a helper it imports)

ENTRY = {'id': 'payments', 'name': 'Payments', 'tool': 'ask_payments', 'resources': 'per_run',
         'accepts': ['code_project_ids'], 'description': 'Ask Payments, an expert agent. Payments.'}
EXPERT_SECRET = 'expert-ssh-secret'


async def ask(subagents, call_id, task, resources=None, entry=ENTRY):
    context = {'config': {**parent(), 'experts': [entry]}, 'credentials': dict(CREDENTIALS), 'workspace': SCRATCH,
               'tools': OFFERED | {entry['tool']}, 'mcp_tools': MCP, 'subagents': subagents}
    token = TOOL_CALL_ID.set(call_id)
    try:
        return await subagents.ask(context, entry, task, resources, call_id)
    finally:
        TOOL_CALL_ID.reset(token)


def test_an_expert_runs_with_the_loaded_configuration_and_is_recorded_with_its_agent_id(): ...
def test_an_expert_that_is_no_longer_available_is_a_tool_error(): ...
def test_a_resource_the_caller_lacks_is_refused_before_anything_is_loaded(): ...
def test_a_fixed_expert_uses_its_own_credentials_and_they_are_redacted(): ...
def test_subagents_and_experts_share_the_16_per_turn_limit_and_the_slots(): ...
def test_stop_cancels_a_running_expert_and_clears_its_credentials(): ...
def test_a_failed_expert_returns_a_redacted_failure(): ...
def test_experts_are_offered_only_to_a_run_with_the_turns_host_and_never_inside_an_expert_or_subagent(config, responses_api): ...
def test_load_resolves_per_run_resources_against_the_expert_and_refuses_unavailable_experts(store, saved_agent): ...
```

Each test body asserts exactly what its name says:
- runs: `load_expert` stand-in receives `(ENTRY, {'code_project_ids': ['worker'], 'collection_ids': [], 'server_ids': [], 'service_ids': []})`; `fake.calls[0]['config']` is what it returned; the answer is the fake's final output, redacted; `recorder.events[0] == ('input', {'task': 'Map payments', 'expert': {'id': 'payments', 'name': 'Payments'}, 'parent_call_id': 'e1'})`; every expert event has `parent_call_id == 'e1'` and agent id `payments` (the fake recorder must capture the agent id passed to `add`; see Step 3).
- unavailable: `load_expert` raises `ValueError(UNAVAILABLE.format(name='Payments'))` → `ask` raises that `ValueError` (the adapter turns it into the tool error), `started` back to its value before the call.
- resource the caller lacks: `{'code_project_ids': ['nope']}` → `ValueError('Code project nope is not one of your resources.')` and `load_expert` never called.
- fixed expert credentials: `load_expert` returns credentials `{'ssh': EXPERT_SECRET}`; the fake's answer contains `EXPERT_SECRET`; the returned answer and every recorded payload show `[redacted]`; after the call the returned credentials dict is empty.
- shared limit: 15 `run_subagent` calls + 1 expert call succeed; the 17th call of either kind raises `ValueError(LIMIT)`.
- Stop: an expert blocked on an event; `await subagents.close()` cancels it, its credentials dict is emptied, no event recorded after close.
- failure: the fake raises `RuntimeError(f'boom {EXPERT_SECRET}')` → result `FAILED.format(name='Payments', message='boom [redacted]')`.
- offered: `agent(config_with_experts, …, subagents=host)` exposes a tool named `ask_payments` with the entry's description and, for a per-run entry, a `resources` property in its schema (fixed: only `task`); without `subagents` the tool is absent; `subagent_config(...)` returns `experts == []` and refuses `tools=['ask_payments']` with `Tool ask_payments may not be given to a subagent.`
- load (database): with real agents (Tasks 2–3 helpers): a per-run expert given a code project the caller has but outside the expert's selection → `RunResourceError` message from `resolve`; a disabled expert → `UNAVAILABLE`; the returned config has `experts == []`, no `record`, and `run_resources` equal to the given ids.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/admin-api && uv run --frozen pytest -q tests/test_expert_calls.py`
Expected: FAIL (no `Subagents.ask`).

- [ ] **Step 3: Implement**

`experts.py` additions:

```python
UNAVAILABLE = 'Expert {name} is not available.'
FAILED = 'Expert {name} failed: {message}'


def load(store, project_id, entry: dict[str, Any], resources: Mapping[str, list[str]] | None
         ) -> tuple[dict[str, Any], dict[str, str]]:
    """The expert's configuration for one call (its current settings; per run: resolved with the given resources,
    which the caller already checked are its own) and its decrypted credentials. ValueError: a message for the model."""
    from knowledge_admin.agents import load_credentials  # agents imports this package

    unavailable = ValueError(UNAVAILABLE.format(name=entry['name']))
    try:
        config = get_agent_config(entry['id'], conninfo=store.conninfo)
    except LookupError:
        raise unavailable from None
    with store.connect() as conn:
        row = conn.execute('SELECT expert FROM ka.agents WHERE id=%s', (entry['id'],)).fetchone()
        model = config['model']
        if row is None or not row['expert'] or not config['enabled'] or model is None or not model['enabled'] \
                or not model_fits(model):
            raise unavailable
        if per_run(config):
            config = resolve(conn, project_id, config, RunResources(**(resources or {})))
    config.pop('record', None)
    config['experts'] = []  # an expert never calls experts
    return config, load_credentials(store, config)
```

(imports: `get_agent_config`, `per_run` from `config`; `RunResources`, `resolve` from `run_resources`; `Mapping`.)

`subagents.py`:
- `LIMIT = 'This turn already started 16 helpers.'`.
- Import `from knowledge_admin.knowledge_agents import experts` (experts imports only config and run_resources, so there is no cycle).
- `subagent_config`: `sub['experts'] = []`; in `_given_tools`, a name that is an expert tool (`name in {e['tool'] for e in config.get('experts', [])}`) raises `ValueError(NOT_FOR_SUBAGENTS.format(name=name))` before the `offered` check.
- Make the caller-side resource check reusable: `def check_given(config, resources) -> dict[str, list[str]]` — the loop at the top of `_narrow` (each id must be in `_listed`, raises `NOT_GIVEN`), returning the de-duplicated ids per kind key; `_narrow` calls it.
- `SubagentEvents.__init__(…, agent_id: str | None = None)`; `add` passes it: `self.recorder.add(kind, {...}, agent_id=self.agent_id)` — extend `RunRecorder.add(kind, payload, agent_id=None)` in `run_record.py` so an event row can carry a different `agent_id` than the recorder's default (store it per event; `flush` writes it). The test `Recorder` fake must accept the keyword.
- `Subagents.__init__` gains `load_expert=None`; new method:

```python
    async def ask(self, context: Mapping[str, Any], entry: Mapping[str, Any], task: str,
                  resources: Mapping[str, list[str]] | None, call_id: str) -> str:
        """One expert call: the expert's answer (redacted), FAILED when its run fails, or a ValueError for a refused
        call (refused calls do not count towards MAX_PER_TURN). Shares the slots and the per-turn limit with run_subagent."""
        check_task(task)
        given = check_given(context['config'], resources) if resources is not None else None
        if self.load_expert is None:
            raise ValueError(experts.UNAVAILABLE.format(name=entry['name']))
        if self.closed or self.parent.closed:
            raise asyncio.CancelledError
        if self.started >= MAX_PER_TURN:
            raise ValueError(LIMIT)
        self.started += 1  # before the load (an await): calls arriving together cannot pass the check twice
        try:
            config, credentials = await asyncio.to_thread(self.load_expert, dict(entry), given)
        except BaseException:
            self.started -= 1  # a refused call does not count
            raise
        self.issued.append(credentials)
        if self.closed or self.parent.closed:  # the turn ended while the expert loaded
            credentials.clear()
            raise asyncio.CancelledError
        secrets = list(dict.fromkeys([*self.secrets, *(value for value in credentials.values() if value)]))
        self.secrets[:] = secrets  # the turn's list: the caller's record redacts the expert's secrets too
        try:
            events = SubagentEvents(self.parent, call_id, secrets=self.secrets, record_usage=self.record_usage,
                                    host=self, agent_id=str(entry['id']))
            events.add('input', {'task': task, 'expert': {'id': entry['id'], 'name': entry['name']}})
            ...  # the same slot / flush / create_task / wait / result / failure block as run(), with
                 # run_agent(config, task, credentials=credentials, …) and FAILED → experts.FAILED.format(name=entry['name'], message=…)
        finally:
            credentials.clear()
```

Refactor the shared block of `run` and `ask` into one private coroutine `_drive(self, events, config, task, credentials, failed: Callable[[str], str]) -> str` so it is not duplicated. (`self.secrets` is the turn's list passed by `NativeRunner.run_turn` — extending it in place is what makes the parent recorder and the final answer redact the expert's secrets.)

`tools/sdk.py`:

```python
class ExpertResources(BaseModel):
    model_config = ConfigDict(extra='forbid')
    code_project_ids: list[str] = Field(default_factory=list)
    collection_ids: list[str] = Field(default_factory=list)
    server_ids: list[str] = Field(default_factory=list)
    service_ids: list[str] = Field(default_factory=list)


class ExpertTask(BaseModel):
    model_config = ConfigDict(extra='forbid')
    task: str = Field(description='A complete task: the expert does not see this conversation.')


class PerRunExpertTask(ExpertTask):
    resources: ExpertResources | None = Field(description='The resources the expert works on; only your own.')


def expert_tool(entry: dict[str, Any]) -> FunctionTool:
    """One expert as its own tool (ask_<name>): the call goes to the turn's Subagents host (Subagents.ask)."""
    arguments = PerRunExpertTask if entry['resources'] == 'per_run' else ExpertTask

    async def run(context: dict[str, Any], value: BaseModel) -> str:
        host = context.get('subagents')
        if host is None:
            raise ValueError(NOT_OFFERED.format(name=entry['tool']))
        resources = getattr(value, 'resources', None)
        return await host.ask(context, entry, value.task, resources.model_dump() if resources else None,
                              TOOL_CALL_ID.get() or '')

    return function_tool(ToolSpec(name=entry['tool'], group='experts', label=entry['name'], summary='',
                                  usage=entry['description'], arguments=arguments, run=run, needs=None),
                         entry['description'])
```

(Read `ToolSpec`'s actual fields in `catalog.py` and construct it accordingly, or factor the body of `function_tool` into a helper taking `name, description, arguments, run` so both use it — prefer the helper if `ToolSpec` requires catalog-only fields.)

`agents.create_agent`: after `tools = [function_tool(spec, usage) for spec, usage in offered]`:

```python
    if subagents:
        tools += [expert_tool(entry) for entry in config.get('experts', [])]
```

`native_sessions.NativeRunner.run_turn`: construct `Subagents(…, load_expert=lambda entry, given: experts.load(self.store, str(self.session['project_id']), entry, given))` (import inside, as `Subagents` is).

- [ ] **Step 4: Run tests**

Run: `cd services/admin-api && uv run --frozen pytest -q tests/test_expert_calls.py tests/test_subagents.py tests/test_subagent_config.py tests/test_native_sessions.py tests/test_function_tools.py tests/test_run_record*.py` → PASS. Update `test_subagents.py`'s limit test for the new `LIMIT` text.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src services/admin-api/tests/test_expert_calls.py services/admin-api/tests/test_subagents.py
git commit -m "feat(admin-api): agents call experts as tools; experts share the subagent slots, limit, Stop and recording"
```

---

### Task 5: The expert's calls in Agent runs (admin-api)

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/agent_runs_api.py`
- Test: `services/admin-api/tests/test_agent_runs_api.py` (add tests)

**Interfaces:**
- Consumes: expert events recorded in callers' turns (`input` with `payload.expert`, `agent_id` = expert, `parent_call_id`).
- Produces:
  - `GET /api/agent-runs?agent_id=<expert>` returns, besides the expert's own runs, rows `{'run_key', 'agent_id': <expert>, 'agent': <expert name>, 'kind': 'expert_call', 'title': <task, first 200 chars>, 'user', 'started_at', 'status': 'completed'|'failed'|'working', 'total_tokens', 'tool_counts', 'id', 'call_id', 'caller': <caller agent name>}`, merged into one list ordered by `id DESC`, with the same `before`/`limit` paging (on `id`).
  - `GET /api/agent-runs/{run_key}?call=<call id>` returns only that call's events: those with `payload.parent_call_id = call`, plus the caller's `tool_call`/`tool_result` events with `payload.call_id = call`.

- [ ] **Step 1: Write the failing tests**

Insert events directly (as `test_agent_runs_api.py` does): a caller run `cc:<session>:1` with `start` (agent `Lead`), a `tool_call` `{'name': 'ask_payments', 'call_id': 'e1'}`, expert events `input {'task': 'Map payments', 'expert': {...}, 'parent_call_id': 'e1'}` (agent_id `payments`), `usage {'total_tokens': 120, 'parent_call_id': 'e1'}`, `tool_call {'name': 'read_file', 'parent_call_id': 'e1'}`, then the caller's `tool_result {'call_id': 'e1', 'preview': 'Answer'}`. Assert:
- `GET /api/agent-runs?agent_id=payments` → one row with `kind == 'expert_call'`, `title == 'Map payments'`, `caller == 'Lead'`, `call_id == 'e1'`, `total_tokens == 120`, `tool_counts == [{'name': 'read_file', 'calls': 1}]`, `status == 'completed'`; a result preview starting with `Expert Payments failed: ` gives `failed`; no result yet gives `working`.
- `GET /api/agent-runs/<run_key>?call=e1` → exactly the expert's events plus the caller's call and result, in seq order.
- `GET /api/agent-runs?agent_id=lead` still lists the caller's run as before (no expert_call rows for it).

- [ ] **Step 2: Run to verify failure**, **Step 3: Implement** — an `EXPERT_CALLS` query over `ka.agent_run_events i WHERE i.kind='input' AND i.agent_id=%(agent_id)s AND i.payload ? 'expert'`, joining the run's `start` event for the caller name and actor, summing `usage` tokens and counting `tool_call`s with `payload->>'parent_call_id' = i.payload->>'parent_call_id'`, and the caller's `tool_result` with that `call_id` for the status; run it only when `agent_id` is given and `kind` is NULL or `'expert_call'`; merge with `RUNS` rows in Python by `id DESC` and cut to `limit`. `record(run_key, call: str | None = Query(None))` filters as above. **Step 4: Run** `uv run --frozen pytest -q tests/test_agent_runs_api.py` → PASS. **Step 5: Commit** `feat(admin-api): Agent runs lists calls made to an expert and opens one call's record`.

---

### Task 6: UI — the expert switch, the Experts group and the badge

**Files:**
- Modify: `services/admin-ui/app/lib/profiles.ts` (`expert?: boolean` on the profile type and in the save body, like `subagents`)
- Modify: `services/admin-ui/app/components/AgentProfileSettings.tsx` (switch next to May start subagents; When to use required while on)
- Modify: `services/admin-ui/app/lib/tools.ts` (`ToolGroup` gains `'experts'`; `TOOL_GROUPS` gains `['experts', 'Experts']` after agents; `AgentTool.resources?: 'fixed' | 'per_run'`; `AgentTools.expert?: boolean`; constants `EXPERT_HELP`, `NO_EXPERTS_NOTE`, `EXPERT_SELF_NOTE`; `impliedTools` keeps excluding non-catalog groups)
- Modify: `services/admin-ui/app/components/AgentToolsView.tsx` (Experts group: rows without Edit usage, a `Fixed`/`Per run` badge, empty note, self note)
- Modify: the All agents list component (find where `tools_summary`/`original_name` are rendered, e.g. `components/AgentsView.tsx` or `AgentCatalog*`) and the agent header (`AgentDetailView.tsx`): `Expert` badge when `expert`
- Test: `services/admin-ui/app/components/__tests__/agentExperts.test.tsx` (new), adjust `agentTools.test.tsx` fixtures if their typed objects need the new fields

**Interfaces:**
- Consumes: profile `expert`, Tools response `expert` and rows with group `experts` (Tasks 2–3).

- [ ] **Step 1: Write the failing tests** (follow `agentTabs.test.tsx` / `agentTools.test.tsx` for rendering and API mocks):
  - General: the `Offer as an expert` switch shows the help text; switching it on with an empty When to use disables Save and shows `Experts need When to use.`; saving sends `expert: true`.
  - Tools tab: an `Experts` group lists `Payments` with its When to use, a `Per run` badge, a switch that is on, and no `Edit usage` button; switching it off and saving sends `{tool: 'expert:p1', mode: 'off', usage: null, subagent_allowed: false}`; with no expert rows the note `No experts yet. Turn on Offer as an expert on an agent's General tab.` shows; when the response has `expert: true` the note `When this agent runs as an expert it cannot call experts.` shows.
  - All agents: a row with `expert: true` shows the `Expert` badge.
- [ ] **Step 2: Run** `cd services/admin-ui && npx vitest run app/components/__tests__/agentExperts.test.tsx` → FAIL.
- [ ] **Step 3: Implement** as listed under Files (copy verbatim from Global Constraints). In `ToolRow`, hide `Edit usage` and the `<code>` name for `group === 'experts'` and render `<Badge variant="outline">{tool.resources === 'per_run' ? 'Per run' : 'Fixed'}</Badge>`.
- [ ] **Step 4: Run** the new test plus `agentTools.test.tsx agentTabs.test.tsx agentTypes.test.tsx`, then `npx tsc --noEmit` → PASS.
- [ ] **Step 5: Commit** `feat(admin-ui): Offer as an expert on General, the Experts group on Tools and the Expert badge`.

---

### Task 7: UI — experts in the run workspace, Agent runs, and the Generate documents prompt

**Files:**
- Modify: `services/admin-ui/app/lib/turns.ts` (`Subagent.expert: { id: string; name: string } | null`; helper starts on a tool call whose name starts with `ask_` as on `run_subagent`; the `input` event's `payload.expert` sets it; failure prefixes add the regex `/^Expert .+ failed: /`)
- Modify: `services/admin-ui/app/components/RunWorkspace.tsx` (Worked line `Asked <name> · <task> · … · <status>` with Open; sidebar `Expert · <name> · <task>`; breadcrumb `Main agent › <name>: <task>`)
- Modify: `services/admin-ui/app/lib/runs.ts` and `components/RunsView.tsx` (row kind `expert_call` labelled `Called as expert by <caller>`, link to the record with `?call=<call_id>`); `RunRecordView.tsx` passes `call` through to the API
- Modify: `services/admin-ui/app/lib/generations.ts` (`DEFAULT_SYSTEM_PROMPT` gains the split guidance)
- Test: `app/lib/__tests__/turns.test.ts` (add), `app/components/__tests__/runWorkspace.test.tsx` (add), the runs view test file (add), `generationForm` test if it asserts the default prompt

**Interfaces:**
- Consumes: event shapes from Task 4, runs API from Task 5.

`DEFAULT_SYSTEM_PROMPT` becomes (one string, verbatim):

```ts
export const DEFAULT_SYSTEM_PROMPT = 'You write technical documentation for software systems. Read the code, documents and servers you were given before you write. Cite evidence as file:line, document path or command. Say what you could not verify.\n\nWhen the document covers several parts (services, code projects, servers), plan the sections first. For each part, ask an expert when its description fits; otherwise start a subagent with run_subagent. Give each a complete task: the section\'s purpose, what to cover, and only that part\'s resources. Write small single-part documents yourself. Combine the answers into one consistent document; check that they agree and resolve or mark conflicts.';
```

- [ ] **Step 1: Write the failing tests:**
  - `groupTurns`: events `tool_call {name: 'ask_payments', call_id: 'e1', arguments: {task: 'Map payments'}}`, `input {task: 'Map payments', expert: {id: 'p', name: 'Payments'}, parent_call_id: 'e1'}`, an expert `tool_call` and `usage`, then `tool_result {call_id: 'e1', preview: 'Done'}` → one subagent with `expert: {id: 'p', name: 'Payments'}`, `status: 'done'`, its work and usage; a result `Expert Payments failed: boom` → `failed` with that error; a plain `run_subagent` helper still has `expert: null`.
  - Workspace: the Worked block shows `Asked Payments · Map payments · 1 tool call · … · done` with an `Open` button; the sidebar shows `Expert · Payments`; opening shows the breadcrumb `Payments: Map payments`.
  - Agent runs: an `expert_call` row shows `Called as expert by Lead` and links to `/activity/runs/<run_key>?call=e1` (use the existing record route shape from `lib/runs.ts`).
- [ ] **Step 2: Run** them → FAIL. **Step 3: Implement.** **Step 4: Run** the changed test files and `npx tsc --noEmit` → PASS. **Step 5: Commit** `feat(admin-ui): experts in the run workspace and Agent runs; Generate documents splits work by default`.

---

### Task 8: Documentation

**Files:**
- Modify: `docs/user-guide.md` (Agents: Offer as an expert, the Experts group, how calls show in a run and in Agent runs; Generate documents: subagents and experts by default)
- Modify: the agents/tools documentation the repo keeps next to the code (`services/admin-api/src/knowledge_admin/tools/README.md`: expert tools are not catalog tools; how they are built and offered) and the database/migrations docs that list migrations (search `0084_remove_claude_code` in `docs/` and add `0085_agent_experts` beside it)
- Modify: `deploy/README.md` only if it lists migration notes per version (rollback of 0085 deletes callers' expert choices)

- [ ] **Step 1:** `grep -rn "0084_remove_claude_code\|run_subagent" docs deploy services/admin-api/src/knowledge_admin/tools/README.md` and update each place that describes the feature set.
- [ ] **Step 2:** Run `python3 -m unittest discover -s tests` from the worktree root (docs/deploy checks) → OK.
- [ ] **Step 3: Commit** `docs: agent experts`.
