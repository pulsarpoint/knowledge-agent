# Clone Agents Instead of Versions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An agent has **one configuration**: what its tabs show is what runs. Agent configuration versions
(`ka.agent_versions`, the Versions tab, Test vN, Choose version, drafts, activation) and agent prompt-version history
(`ka.agent_prompts`, `ka.agent_prompt_versions`, the Prompts tab, `/api/llm/prompts`) are removed. The prompt becomes
one text field, `ka.agents.system_prompt`, edited on **General**. Experiments are made on a **clone**: **Clone agent**
copies the whole configuration (credentials referenced, history not copied), **Apply to <original>** copies the
clone's configuration back (everything except name and description, revision- and fingerprint-checked, refused while
the original is working), and feedback suggestions from a Test chat are applied **to this agent** or **to a new
clone**. The migration first makes every agent's active version (what runs today) its own configuration, then drops
the versions, the prompt history, the version references and their triggers, constraints and functions. Docs →
Prompts (the documentation prompt library, `ka.documentation_prompts`) is not touched.

**Architecture:**
- **Schema, in two migrations so code never reads a dropped table:**
  - `0079_agent_system_prompt` (Task 1): `ka.agents.system_prompt text NOT NULL DEFAULT ''` (≤ 100 000 characters)
    filled from each agent's active prompt version; drops the deferred `agent_system_prompt_fk` (an agent no longer
    needs prompt rows). Its down migration gives every agent its prompt back as the active prompt version (a new
    version when the text was edited after 0079) and restores the FK.
  - `0080_clone_agents` (Task 3): **materialises** every active configuration version into its agent (columns,
    selections, settings rows, per-server overrides, agent SSH connections, tools; versions saved before 0078 get
    0078's implied-tools rule), then drops `ka.agent_versions`, `ka.agent_prompts`, `ka.agent_prompt_versions`, the
    view `agent_prompt_version_details`, `guard_prompt_version()`, `agents.active_config_version_id`,
    `agents.next_config_version`, `agent_sessions.agent_version_id` and their constraints, and adds
    `agents.cloned_from text REFERENCES agents(id) ON DELETE SET NULL`. Down restores the empty 0079 schema.
- **Code stops reading prompt tables in Task 1** (config, wizard, profile, `knowledge_llm.prompts`; the Prompts API is
  deleted) **and versions in Tasks 2–3** (Task 2: no conversation chooses a version; Task 3: the loader has no version
  branch, the versions API is deleted, the list has no overlay; in the same commit as 0080's drop).
- **One comparison for "runs the same":** `knowledge_agents.config.sections(config)` — the configuration by section
  without identity, revisions or live data. The migration test, Clone, Apply's change list and its fingerprint all
  use it.
- **`knowledge_admin/agent_clones.py`** (Tasks 4–5): `clone_name`, `copy_configuration(conn, source, target, actor)`
  (the only place that knows every agent-owned table), `clone(conn, source, actor)`; routes `POST
  /api/agents/{id}/clone`, `GET|POST /api/agents/{id}/apply`.
- **`knowledge_admin/agent_reviews.py`** (Tasks 3, 6): the tuning reviewer's output models (moved from the deleted
  `agent_versions.py`), `write_changes`, routes `GET /api/agents/{id}/reviews/{review_id}` and `POST …/apply`.
- **admin-ui:** `AgentPromptCard` on General (Task 7); no Prompts/Versions tabs or their libs; `SuggestionApply`
  sheet and a Test tab without versions (Task 8); `AgentActions` (header menu **Clone agent**, **Apply to
  <original>**, **Delete this clone**), "Clone of <original>" on General and in All agents, a **Clone** row action
  (Task 9).

**Tech Stack:**
- PostgreSQL 17 with pgvector, golang-migrate SQL migrations, `db/tests/run.sh`;
- FastAPI, pydantic v2, psycopg3, pytest against the disposable PostgreSQL fixture
  (`knowledge-agent-v3-postgres:17.10-vector0.8.6`);
- React Router 7 with shadcn/ui, Vitest with Testing Library (`services/admin-ui`).

**Spec:** `docs/superpowers/specs/2026-10-08-clone-agents-design.md` (binding).

## Global Constraints

- Implementers and reviewers never run make targets, never touch .env or .local, never git stash. Never touch the
  running Docker stack's database: tests use the disposable PostgreSQL fixture (`tests/conftest.py`) and
  `db/tests/run.sh`.
- Commit trailer: `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Python stays compatible with 3.12. TypeScript passes `pnpm exec tsc --noEmit` (tests are type-checked too).
- Every task leaves `cd services/admin-api && uv run --frozen pytest -q`, `bash db/tests/run.sh` and
  `cd services/admin-ui && pnpm exec tsc --noEmit && pnpm exec vitest run` green. Each task lists the files it
  touches; run the whole suites before committing.
- Migrations: `.up.sql` and `.down.sql`, each `BEGIN; … COMMIT;` with `SET search_path = ka, public;` and `SET
  CONSTRAINTS ALL IMMEDIATE;` before any `ALTER TABLE agents` (deferred FK events would block it). Rollback pin in
  `db/tests/run.sh`: `77` → `78` (Task 1) → `79` (Task 3).
- Credentials: a clone **references** the original's saved SSH credentials (`agent_server_connections.credential_id`);
  nothing is decrypted, copied or logged. Deleting an agent keeps a credential another agent still references (the
  existing `NOT EXISTS` check in `profiles_api.delete`).
- Never copied by Clone or Apply: sessions, runs, run records, reviews, audit, `name`, `description`, `cloned_from`;
  Apply also keeps the original's `enabled`.
- Texts (API):
  - `profiles_api.PROMPT_STALE = 'Agent changed. Reload before saving.'` (409, the profile's existing text);
  - prompt validation → 400 (pydantic; the app maps validation errors to 400);
  - `agents.get_prompt`: `'The agent requires a system prompt.'`;
  - `agent_clones.NAME_TAKEN = 'An agent with this name already exists.'` (409);
  - `agent_clones.NOT_A_CLONE = 'This agent is not a clone.'` (404; also after the original was deleted);
  - `agent_clones.APPLY_WORKING = 'The original agent is working right now. Wait for its documentation sessions and runs to finish, then apply.'` (409);
  - `agent_clones.ORIGINAL_CHANGED = 'The original changed; review again.'` (409);
  - `agent_reviews.REVIEW_NOT_FOUND = 'Suggestion not found for this agent.'` (404);
  - `agent_reviews.AGENT_CHANGED = 'The agent changed since you opened the suggestion. Review it again.'` (409);
  - `agent_reviews.UNKNOWN_SERVER = 'Instructions refer to a server outside this agent.'` (422);
  - `agent_reviews.PROMPT_REQUIRED = 'Enter a system prompt.'` (400).
- Section labels (`config.SECTIONS`, in this order): `Prompt`, `Instructions`, `When to use`, `Model`, `Agent type`,
  `Resources`, `Tools`, `Skills`, `Documents`, `Code projects`, `SSH`, `MCP`, `Services`.
- UI texts: card `System prompt`, `Save prompt`, `Discard prompt changes`, `System prompt saved.`; header menu
  trigger `Actions for <name>`, item `Clone agent`, toast `Clone created: <name>`; `Clone of <original>`; button
  `Apply to <original>`, dialog title `Apply to <original>?`, description `These sections of <original> are replaced
  by this clone’s: <labels>. Its name and description stay.` or `The original already runs this configuration.`,
  action `Apply`, status `Applied to <original>.`, button `Delete this clone`, dialog `Delete this clone?`, action
  `Delete clone`; list row button `Clone <name>`; Test tab `Current agent configuration` / `Saved session
  configuration`; feedback button `Review and apply`, sheet `Apply suggestions`, buttons `Apply to this agent`,
  `Apply to a new clone`, notice `Applied to this agent. New conversations use it.`

## Review Focus

1. **An agent runs something else after the migration than before.** Examples: the active version's prompt,
   instructions, model, type, Claude Code tools, resources mode, `when_to_use`, capability switches, selections,
   per-server instruction/skill/MCP overrides (`instructions_source = 'agent_version'`), agent SSH connections
   (username, port, sudo, known hosts, credential) or tools not carried over; a version saved before 0072/0075/0078
   not getting the defaults the loader gave it (all Claude Code tools, `fixed`, implied tools); agents without a
   version changed; the active prompt (not the newest draft) not moved. Pinned in:
   - Task 1: `test_up_moves_each_active_prompt_into_its_agent`;
   - Task 3: `test_every_agent_runs_what_it_ran_before`, `test_a_claude_code_agent_keeps_its_versions_claude_code_tools`,
     `test_pinned_conversations_keep_their_configuration`.
2. **A clone differs from, or leaks into, its original.** Examples: a section or override missing in the clone; the
   saved SSH key duplicated, or deleted with the original while the clone uses it; sessions, runs or history copied;
   names colliding or exceeding 100 characters; `enabled` not kept. Pinned in Task 4:
   `test_a_clone_runs_exactly_what_its_original_runs`, `test_a_clone_is_named_after_its_original_and_keeps_its_switch`,
   `test_clone_names_stay_within_the_name_limit`, `test_history_stays_with_the_original_and_the_clone_outlives_it`.
3. **Apply writes the wrong thing or at the wrong time.** Examples: name, description, `enabled` or `cloned_from`
   overwritten; a stale dialog overwriting newer edits (including an instructions edit that does not bump
   `agents.revision`); applying while the original runs; applying to a deleted original; no revision bump or audit.
   Pinned in Task 5: `test_apply_copies_everything_but_name_and_description`,
   `test_apply_is_refused_when_the_original_changed_or_is_working`, `test_apply_needs_a_clone_whose_original_still_exists`.
4. **A feedback suggestion changes more than reviewed, or another agent.** Examples: resource selections narrowed by
   a per-run conversation's pinned config; a server outside the agent overridden; another agent's review applied;
   a blank prompt saved; "Apply to a new clone" also changing the original. Pinned in:
   - Task 6: `test_a_suggestion_is_applied_to_this_agent_after_review`,
     `test_server_instructions_become_an_override_and_unknown_servers_are_refused`,
     `test_a_suggestion_applied_to_a_new_clone_leaves_the_agent_as_it_was`,
     `test_a_suggestion_from_a_per_run_chat_never_narrows_the_agent`;
   - Task 8: `prefills the proposed texts, lets inheriting servers follow and applies to this agent`,
     `applies to a new clone and keeps the edits when applying fails`.
5. **Leftovers of versions or prompt history, or a broken rollback.** Examples: an endpoint still accepting
   `version_id`; a screen showing Prompts, Versions, Test vN, Choose version, `Agent vN` or `Prompt vN`; a down
   migration that leaves 0078 code without prompt rows or the prompt FK; historical migration tests or
   `db/tests/run.sh` reading dropped tables. Pinned in:
   - Task 1: `test_down_gives_every_agent_its_prompt_as_the_active_version`, `test_the_prompts_api_is_gone`;
   - Task 2: `test_no_conversation_chooses_a_version`;
   - Task 3: `test_up_drops_versions_and_prompt_history_and_down_restores_the_empty_schema`, `db/tests/run.sh`;
   - Task 7: `the agent page has no Prompts or Versions tab`; Task 8: `a new test sends no version`.

## Decisions (where the spec leaves a choice)

- **Two migrations.** 0079 adds the prompt column (prompt tables stay readable until 0080, so every task is green and
  old down migrations keep working); 0080 materialises and drops in one transaction, in the same commit that removes
  the last version reader.
- **Prompt length 100 000** (the versions' limit, so materialising an activated long draft cannot fail). The wizard
  keeps 40 000. 0079 down truncates to 40 000 when it restores a prompt version (0078's limit).
- **Materialised fields:** everything `sections()` compares, plus the selection rows behind them. Name, description
  and `enabled` stay the agent's (identity and on/off are not configuration; the list overlay never showed a
  version's `enabled`). `when_to_use` is materialised. Shared resources (a skill's text, a server's hostname, a
  collection's paths) are live data, as they already are for agents without a version; selections of deleted or
  archived resources are dropped, as the loader would have dropped them.
- **`system_prompt` in the config stays `{'content': text}`** so pinned sessions (which hold the old
  `{id,title,version,revision,content}` shape) and `agents.get_prompt` / `claude_code.agent_system_prompt` keep
  reading `['content']`.
- **Stale checks use `revision` + `fingerprint`** (`sha256` of `sections()`): an instructions or SSH save bumps
  `operations_settings.revision`, not `agents.revision`, so the revision alone would miss it.
- **Apply keeps the original's `enabled`**: switching a clone off must never switch the original off.
- **A new server override written by a suggestion inherits skills and MCP** (`inherit_skills = inherit_mcp_servers =
  true`); an existing override keeps its flags.
- **All agents row actions are icon buttons today (Open, Remove)**; Clone is added as a third icon button `Clone
  <name>`. The agent page header gets a menu (`Actions for <name>`) with **Clone agent**.
- **`/llm-settings/prompts?agent=…` still redirects**, now to `/agents/<agent>?view=general`.

## Delivery and test boundaries

Tasks land in order on `feature/clone-agents`; every suite is green after each task. Task 1 (prompt column) stands
alone. Task 2 (no version per conversation) stands alone. Task 3 (0080) needs Tasks 1–2. Task 4 (clone) needs Task 3
(`cloned_from`, `sections`). Task 5 (apply) needs Task 4. Task 6 (suggestions) needs Tasks 3–4. Task 7 (General,
tabs) needs Task 1's API. Task 8 (Test tab, suggestions UI) needs Tasks 2 and 6. Task 9 (clone UI) needs Tasks 4–5
and 7. Task 10 updates the docs and runs every suite.

---

### Task 1: The prompt is one field on the agent (0079), the Prompts API goes

**Files:**
- Create: `db/migrations/0079_agent_system_prompt.up.sql`, `db/migrations/0079_agent_system_prompt.down.sql`
- Modify: `db/tests/run.sh` (rollback pin `77` → `78`)
- Modify: `services/admin-api/src/knowledge_admin/knowledge_agents/config.py`, `agents.py`, `agent_wizard.py`,
  `profiles_api.py`, `api.py`
- Delete: `services/admin-api/src/knowledge_admin/prompts_api.py`
- Modify: `packages/llm-settings/src/knowledge_llm/prompts.py`
- Create: `services/admin-api/tests/test_agent_system_prompt_migration.py`, `services/admin-api/tests/test_agent_prompt.py`
- Delete: `services/admin-api/tests/test_prompts.py`
- Modify tests: `test_agents.py`, `test_profiles.py`, `test_operations.py`, `test_agent_wizard.py`,
  `test_claude_code_agents.py`, `test_claude_code_migration.py`, `test_knowledge_agents_migration.py`

**Interfaces:**
- Produces: `ka.agents.system_prompt`; config `system_prompt == {'content': <text>}`; `PUT /api/agents/{id}/prompt`
  `{revision, system_prompt}` → the profile (`GET /api/agents/{id}` now carries `system_prompt`, no `active_prompt`);
  `profiles_api.PROMPT_LIMIT = 100000`; `knowledge_llm.prompts.active_prompt(agent)` reads the column.
- Removed: `/api/llm/prompts…`; `prompt_title`, `prompt_version` in `GET /api/agents`.

- [ ] **Step 1: Write the failing tests**

Create `services/admin-api/tests/test_agent_system_prompt_migration.py`:

```python
"""0079: an agent's prompt is one text field filled from its active prompt version; prompt rows are no longer
required. Down gives every agent its prompt back as the active prompt version."""
from pathlib import Path
from uuid import uuid4

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0079_agent_system_prompt.up.sql', '0079_agent_system_prompt.down.sql'
# Later migrations roll back first, newest first (0080 restores the prompt tables empty).
LATER = sorted((path.name for path in MIGRATIONS.glob('*.down.sql') if path.name[:4] > '0079'), reverse=True)
PLACEHOLDER = "Follow the administrator's instructions."


def run(conn, name: str):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def active(conn, agent_id: str):
    return conn.execute('''SELECT v.version,v.title,v.content,p.next_version FROM ka.agent_prompts p
        JOIN ka.agent_prompt_versions v ON v.id=p.active_version_id WHERE p.agent=%s''', (agent_id,)).fetchone()


def has_fk(conn) -> bool:
    return conn.execute("SELECT 1 FROM pg_constraint WHERE conname='agent_system_prompt_fk'").fetchone() is not None


def has_column(conn) -> bool:
    return conn.execute("""SELECT 1 FROM information_schema.columns WHERE table_schema='ka' AND table_name='agents'
        AND column_name='system_prompt'""").fetchone() is not None


def test_up_moves_each_active_prompt_into_its_agent(store):
    with store.connect() as conn:
        for name in [*LATER, DOWN]:
            run(conn, name)
        conn.commit()
        assert has_fk(conn) and not has_column(conn)
        version = uuid4()
        conn.execute("""INSERT INTO ka.agent_prompt_versions(id,agent,version,title,content,published_at)
            SELECT %s,'search',next_version,'Evidence','Answer with evidence.',now() FROM ka.agent_prompts
            WHERE agent='search'""", (version,))
        conn.execute("UPDATE ka.agent_prompts SET active_version_id=%s,next_version=next_version+1 WHERE agent='search'",
                     (version,))
        conn.execute("""INSERT INTO ka.agent_prompt_versions(agent,version,title,content)
            SELECT 'code',next_version,'Draft','Never runs.' FROM ka.agent_prompts WHERE agent='code'""")  # a draft
        conn.commit()
        expected = {row['id']: row['content'] for row in conn.execute('''SELECT a.id,v.content FROM ka.agents a
            JOIN ka.agent_prompts p ON p.agent=a.id JOIN ka.agent_prompt_versions v ON v.id=p.active_version_id''')}
        run(conn, UP)
        conn.commit()
        assert {row['id']: row['system_prompt'] for row in conn.execute('SELECT id,system_prompt FROM ka.agents')} == expected
        assert expected['search'] == 'Answer with evidence.' and expected['code'] != 'Never runs.'
        assert not has_fk(conn)
        conn.execute("INSERT INTO ka.agents(id,name,system_prompt) VALUES ('fresh','Fresh','Be brief.')")  # no prompt rows
        conn.commit()


def test_down_gives_every_agent_its_prompt_as_the_active_version(store):
    with store.connect() as conn:
        for name in LATER:
            run(conn, name)
        conn.commit()
        conn.execute("INSERT INTO ka.agents(id,name,system_prompt) VALUES ('fresh','Fresh','Be brief.'),('blank','Blank','')")
        conn.execute("UPDATE ka.agents SET system_prompt='Edited after 0079.' WHERE id='search'")
        conn.commit()
        run(conn, DOWN)
        conn.commit()
        assert has_fk(conn) and not has_column(conn)
        assert active(conn, 'fresh') == {'version': 1, 'title': 'System prompt', 'content': 'Be brief.', 'next_version': 2}
        assert active(conn, 'blank')['content'] == PLACEHOLDER
        assert active(conn, 'search')['content'] == 'Edited after 0079.'
        run(conn, UP)
        conn.commit()
        assert conn.execute("SELECT system_prompt FROM ka.agents WHERE id='search'").fetchone()['system_prompt'] == \
            'Edited after 0079.'
```

Create `services/admin-api/tests/test_agent_prompt.py`:

```python
"""The agent's prompt is one text field: saved with the agent's revision, read by every runtime."""
from knowledge_llm.prompts import DEFAULT_PROMPTS, active_prompt

from knowledge_admin.knowledge_agents.config import get_agent_config
from test_api import _client
from test_llm import use_database
from test_profiles import create
from test_repositories import HEADERS


def put_prompt(client, agent_id, text, revision=None):
    if revision is None:
        revision = client.get(f'/api/agents/{agent_id}', headers=HEADERS).json()['revision']
    return client.put(f'/api/agents/{agent_id}/prompt', headers=HEADERS,
                      json={'revision': revision, 'system_prompt': text})


def test_seeded_agents_keep_their_prompt_text(store, tmp_path, monkeypatch):
    client = _client(tmp_path, repository_store=store)
    use_database(monkeypatch, store)
    for agent_id in ('search', 'code', 'operations'):
        assert active_prompt(agent_id) == DEFAULT_PROMPTS[agent_id]
        assert client.get(f'/api/agents/{agent_id}', headers=HEADERS).json()['system_prompt'] == DEFAULT_PROMPTS[agent_id]


def test_the_prompt_is_saved_with_the_agents_revision(store, tmp_path, monkeypatch):
    monkeypatch.setenv('CODE_ROOT', '/work/code')
    monkeypatch.setenv('DATA_ROOT_DIR', '/work/data')
    client = _client(tmp_path, repository_store=store)
    agent = create(client, 'Prompted')
    assert agent['system_prompt'] == 'Instructions for Prompted.' and 'active_prompt' not in agent
    saved = put_prompt(client, agent['id'], 'Answer with evidence.', agent['revision'])
    assert saved.status_code == 200, saved.text
    assert (saved.json()['system_prompt'], saved.json()['revision']) == ('Answer with evidence.', agent['revision'] + 1)
    assert get_agent_config(agent['id'], conninfo=store.conninfo)['system_prompt'] == {'content': 'Answer with evidence.'}
    use_database(monkeypatch, store)
    assert active_prompt(agent['id']) == 'Answer with evidence.'
    stale = put_prompt(client, agent['id'], 'Older edit.', agent['revision'])
    assert stale.status_code == 409 and stale.json()['error']['message'] == 'Agent changed. Reload before saving.'
    for bad in ('', '  \n', 'bad\x00prompt', 'x' * 100001):
        assert put_prompt(client, agent['id'], bad).status_code == 400, repr(bad[:10])
    assert put_prompt(client, 'missing', 'Text', 1).status_code == 404
    assert client.put(f"/api/agents/{agent['id']}/prompt", json={'revision': 2, 'system_prompt': 'x'}).status_code == 403
    with store.connect() as conn:
        audit = conn.execute("SELECT details FROM ka.audit_events WHERE action='agent.prompt.updated'").fetchall()
    assert [row['details'] for row in audit] == [{'agent_id': agent['id']}]


def test_the_prompts_api_is_gone(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    assert client.get('/api/llm/prompts', headers=HEADERS).status_code == 404
    listed = client.get('/api/agents', headers=HEADERS).json()['agents']
    assert not {'prompt_title', 'prompt_version', 'system_prompt'} & set(listed[0])
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_agent_system_prompt_migration.py tests/test_agent_prompt.py -q`
Expected: FAIL: `FileNotFoundError: … 0079_agent_system_prompt.down.sql`, `KeyError: 'system_prompt'`, 405 on `PUT …/prompt`.

- [ ] **Step 3: Write the migration**

`db/migrations/0079_agent_system_prompt.up.sql`:

```sql
BEGIN;
SET search_path = ka, public;
SET CONSTRAINTS ALL IMMEDIATE;

-- An agent's system prompt is one text field: what runs. The active prompt version's text moves into the agent;
-- prompt rows are no longer required (0080 drops them with the configuration versions).
ALTER TABLE agents ADD COLUMN system_prompt text NOT NULL DEFAULT ''
    CONSTRAINT system_prompt_length CHECK (char_length(system_prompt) <= 100000);
UPDATE agents a SET system_prompt = v.content
FROM agent_prompts p JOIN agent_prompt_versions v ON v.agent = p.agent AND v.id = p.active_version_id
WHERE p.agent = a.id;
ALTER TABLE agents DROP CONSTRAINT agent_system_prompt_fk;
COMMENT ON COLUMN agents.system_prompt IS 'The agent''s system prompt, edited on its General tab. What runs.';

COMMIT;
```

`db/migrations/0079_agent_system_prompt.down.sql`:

```sql
BEGIN;
SET search_path = ka, public;
SET CONSTRAINTS ALL IMMEDIATE;

-- 0078 reads the prompt from the active prompt version. A prompt edited after 0079 becomes a new active version;
-- an agent without prompt rows (created after 0079, or after 0080 down) gets its prompt as published version 1.
-- A version needs text (an empty prompt becomes a placeholder) and at most 40 000 characters.
WITH changed AS MATERIALIZED (
    SELECT p.agent, gen_random_uuid() AS version_id, p.next_version AS version, left(a.system_prompt, 40000) AS content
    FROM agents a JOIN agent_prompts p ON p.agent = a.id JOIN agent_prompt_versions v ON v.id = p.active_version_id
    WHERE btrim(a.system_prompt) <> '' AND v.content <> a.system_prompt),
versions AS (INSERT INTO agent_prompt_versions(id, agent, version, title, content, published_at)
    SELECT version_id, agent, version, 'System prompt', content, now() FROM changed)
UPDATE agent_prompts p SET active_version_id = c.version_id, next_version = c.version + 1, revision = p.revision + 1
FROM changed c WHERE p.agent = c.agent;
WITH added AS MATERIALIZED (
    SELECT a.id AS agent, gen_random_uuid() AS version_id,
        coalesce(nullif(btrim(left(a.system_prompt, 40000)), ''), 'Follow the administrator''s instructions.') AS content
    FROM agents a WHERE NOT EXISTS (SELECT 1 FROM agent_prompts p WHERE p.agent = a.id)),
prompts AS (INSERT INTO agent_prompts(agent, active_version_id, next_version) SELECT agent, version_id, 2 FROM added)
INSERT INTO agent_prompt_versions(id, agent, version, title, content, published_at)
SELECT version_id, agent, 1, 'System prompt', content, now() FROM added;
SET CONSTRAINTS ALL IMMEDIATE;

ALTER TABLE agents ADD CONSTRAINT agent_system_prompt_fk FOREIGN KEY (id) REFERENCES agent_prompts(agent)
    DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE agents DROP COLUMN system_prompt;

COMMIT;
```

In `db/tests/run.sh` change `[ "$rolled_back_version" = 77 ]` to `[ "$rolled_back_version" = 78 ]`.

- [ ] **Step 4: Read and write the column**

`knowledge_agents/config.py` (`get_agent_config`, working-copy path): add `system_prompt` to the agent `SELECT`
(after `resources`); delete the `prompt = conn.execute("""SELECT v.id,v.title,…` query and its `ValueError`; in the
returned dict replace `'system_prompt': prompt` with `'system_prompt': {'content': agent['system_prompt']}`. The
version branch stays unchanged in this task.

`agents.py` (`get_prompt`): `raise ValueError('The agent requires a system prompt.')`.

`agent_wizard.py`:
- `inventory()`: `'existing_agents': conn.execute('SELECT name,description,when_to_use FROM ka.agents ORDER BY name').fetchall(),`
- `create()`: `agent_id = str(uuid4())` (drop `prompt_id`); add `system_prompt` to the `INSERT INTO ka.agents(…)`
  column list after `resources` with `value.system_prompt` in the values; delete the two `INSERT INTO
  ka.agent_prompts…` / `ka.agent_prompt_versions…` statements.

`profiles_api.py`:
- `read()`: delete the `row['active_prompt'] = …` statement (`profile()` already returns `system_prompt`).
- `listing()`: remove `v.title AS prompt_title,v.version AS prompt_version,` and the join line `LEFT JOIN
  ka.agent_prompts p ON p.agent=a.id LEFT JOIN ka.agent_prompt_versions v ON v.id=p.active_version_id`; at the top of
  the `for row in rows:` loop add `row.pop('system_prompt')  # read with the agent, not in the list`; delete the two
  `row['prompt_title'] = …` / `row['prompt_version'] = …` lines of the version overlay.
- Add next to `ModelInput`:

```python
PROMPT_LIMIT = 100000
PROMPT_STALE = 'Agent changed. Reload before saving.'


class PromptInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1, strict=True)
    system_prompt: str = Field(min_length=1, max_length=PROMPT_LIMIT)

    @field_validator('system_prompt')
    @classmethod
    def valid_text(cls, value: str) -> str:
        if not value.strip() or '\x00' in value:
            raise ValueError('Enter a system prompt without null characters')
        return value
```

- Add the route after `update`:

```python
    @router.put('/{agent_id}/prompt')
    def prompt(agent_id: str, value: PromptInput, request: Request):
        """The agent's system prompt, saved with its revision: what new conversations and runs use."""
        with database().connect() as conn:
            conn.execute('SELECT id FROM ka.agents WHERE id=%s FOR UPDATE', (agent_id,))
            old = read(conn, agent_id)
            if old['revision'] != value.revision:
                raise HTTPException(409, PROMPT_STALE)
            actor = audit(conn, request, agent_id, 'agent.prompt.updated')
            conn.execute('UPDATE ka.agents SET system_prompt=%s,revision=revision+1,updated_by=%s WHERE id=%s',
                         (value.system_prompt, actor, agent_id))
            return read(conn, agent_id)
```

`api.py`: drop `prompts_api` from the `from knowledge_admin import llm_api, prompts_api, …` line and delete
`router.include_router(prompts_api.build_api(app_name, repository_store))`. `git rm
services/admin-api/src/knowledge_admin/prompts_api.py services/admin-api/tests/test_prompts.py`.

`packages/llm-settings/src/knowledge_llm/prompts.py`:

```python
"""The agent's system prompt, read once at the start of each request."""
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from knowledge_llm import ModelUnavailable, managed

DEFAULT_PROMPTS = {
    agent: (Path(__file__).parent / 'prompts' / f'{agent}.md').read_text(encoding='utf-8').strip()
    for agent in ('search', 'code', 'operations')
}


def active_prompt(agent: str) -> str:
    if not managed():
        return DEFAULT_PROMPTS[agent]
    try:
        with psycopg.connect('', row_factory=dict_row, connect_timeout=5) as conn:
            row = conn.execute('SELECT system_prompt FROM ka.agents WHERE id=%s', (agent,)).fetchone()
    except psycopg.Error:
        raise ModelUnavailable('Agent prompts are unavailable. Check the agent’s General tab.') from None
    if row is None or not row['system_prompt'].strip():
        raise ModelUnavailable('No system prompt. Check the agent’s General tab.')
    return row['system_prompt']
```

- [ ] **Step 5: Update the existing tests**

- `test_agents.py`: in `saved_agent` delete the `draft = client.post('/api/llm/prompts/operations/versions', …)` call
  and its assert; in `test_complete_config_active_prompt_and_secret_locations` replace the prompt assertion with
  `assert config['system_prompt'] == {'content': DEFAULT_PROMPTS['operations']}` (import `from knowledge_llm.prompts
  import DEFAULT_PROMPTS`) and drop `'Never use this draft.'` from the `excluded` tuple.
- `test_profiles.py`: rename `test_catalog_creation_and_independent_prompt_versions` to
  `test_catalog_creation_and_independent_prompts`; replace its lines from `assert first['active_prompt']['id'] != …`
  through the second `active_prompt` assertion with:

```python
    assert (first['system_prompt'], second['system_prompt']) == ('Instructions for Payments.', 'Instructions for Documentation.')
    saved = client.put(f"/api/agents/{first['id']}/prompt", headers=HEADERS,
                       json={'revision': first['revision'], 'system_prompt': 'Payments instructions.'})
    assert saved.status_code == 200, saved.text
    assert client.get(f"/api/agents/{second['id']}", headers=HEADERS).json()['system_prompt'] == second['system_prompt']
    first = saved.json()
```

  In `test_system_prompt_is_required_by_api_and_database` replace the final `ForeignKeyViolation` block with:

```python
    with pytest.raises(psycopg.errors.NotNullViolation):
        with store.connect() as conn:
            conn.execute("INSERT INTO ka.agents(id,name,system_prompt) VALUES ('missing-prompt','Missing prompt',NULL)")
```

- `test_operations.py` (`test_operations_prompt_seed_and_assignment_compatibility`): replace the three
  `/api/llm/prompts` lines with
  `assert client.get('/api/agents/operations', headers=HEADERS).json()['system_prompt'] == DEFAULT_PROMPTS['operations']`.
- `test_agent_wizard.py`: delete `assert config['system_prompt']['version'] == 1`; replace
  `assert agent['active_prompt']['version'] == 1` with `assert agent['system_prompt'] == BLANK['system_prompt']`.
- `test_claude_code_agents.py`: in `test_claude_code_agent_is_listed_with_its_type_prompt_and_tools` replace the
  `listed[...]` assertion with `assert (listed['runtime'], listed['claude_code_tools']) == ('claude_code', ALL_TOOLS)`;
  in `test_switching_the_type_keeps_the_config_and_needs_a_claude_model` replace `switched.json()['active_prompt']['title']`
  / `native['active_prompt']['title']` with `switched.json()['system_prompt']` / `native['system_prompt']`.
- `test_claude_code_migration.py` and `test_knowledge_agents_migration.py`: add
  `LATER = ('0079_agent_system_prompt.down.sql',)  # 0072 down drops the prompt FK that 0079 removed` and, in every
  test, before its first `run(conn, REMOVED)` (or first `run(conn, DOWN)` when there is no `REMOVED`), add
  `for name in LATER: run(conn, name)`. In `test_knowledge_agents_migration.py`
  (`test_claude_code_agents_take_every_capability_but_need_a_prompt`) delete the final
  `with pytest.raises(psycopg.errors.ForeignKeyViolation): …` block (three lines) and rename the test to
  `test_claude_code_agents_take_every_capability`: an agent no longer needs prompt rows.

- [ ] **Step 6: Run the tests**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_agent_system_prompt_migration.py tests/test_agent_prompt.py tests/test_agents.py tests/test_profiles.py tests/test_operations.py tests/test_agent_wizard.py tests/test_claude_code_agents.py tests/test_claude_code_migration.py tests/test_knowledge_agents_migration.py tests/test_documents.py -q`
Expected: PASS.

Run: `bash db/tests/run.sh`
Expected: every `PASS:` line, exit 0.

Run: `cd services/admin-api && uv run --frozen pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add db packages/llm-settings services/admin-api
git commit -m "feat: the agent's prompt is one field (0079); the agent Prompts API goes

ka.agents.system_prompt holds the active prompt version's text; agents no longer need prompt rows; PUT
/api/agents/{id}/prompt saves it with the agent's revision. 0079 down gives every agent its prompt back as the active
prompt version.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: No conversation chooses a version

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/agent_sessions.py`, `profiles_api.py`, `claude_code_api.py`
- Create: `services/admin-api/tests/test_conversations_use_the_agent.py`
- Modify tests: `test_claude_code_sessions.py`, `test_agent_versions.py`, `test_run_resources_test_chats.py`

**Interfaces:**
- Changes: `RunInput` and `SessionInput` have no `version_id` (sending one → 400); `GET /api/agents/{id}/config` and
  `GET /api/agents/{id}/run-resources` take no `version_id`; a Claude Code session's `prompt_snapshot` has no
  `agent_version`. Until Task 3 the loader still honours an active version.

- [ ] **Step 1: Write the failing test**

Create `services/admin-api/tests/test_conversations_use_the_agent.py`:

```python
"""A conversation runs its agent's configuration: no version is chosen per conversation, run or preview."""
from uuid import uuid4

from test_agent_runs import direct, events  # noqa: F401  (fixture)
from test_repositories import HEADERS
from test_sdk_agent import responses_api  # noqa: F401  (fixture)


def test_no_conversation_chooses_a_version(direct):
    client, agent_id, run_path = direct
    version = str(uuid4())
    assert client.post(run_path, headers=HEADERS, json={'task': 'Hi', 'version_id': version}).status_code == 400
    assert client.post(f'/api/agents/{agent_id}/sessions', headers=HEADERS,
                       json={'version_id': version, 'is_test': True}).status_code == 400
    for path in ('config', 'run-resources'):
        response = client.get(f'/api/agents/{agent_id}/{path}', headers=HEADERS, params={'version_id': version})
        assert response.status_code == 200, (path, response.text)  # the parameter is not read
    started = events(client.post(run_path, headers=HEADERS, json={'task': 'Hi'}))[0]
    assert started['context']['config']['id'] == agent_id
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_conversations_use_the_agent.py -q`
Expected: FAIL: the first post returns 404 (`Version not found for this agent.`), not 400.

- [ ] **Step 3: Remove the version inputs**

`agent_sessions.py`:
- `RunInput` and `SessionInput`: delete `version_id: UUID | None = None`.
- `start_turn`: delete the two lines `if value.version_id is not None and value.version_id != saved['agent_version_id']:`
  / `raise HTTPException(409,'A session cannot change agent versions. Start a new session.')`; the new-conversation
  branch becomes `config = await asyncio.to_thread(get_agent_config,agent_id,conninfo=store.conninfo)`.
- `create`: `config = get_agent_config(agent_id, conninfo=db.conninfo)`.

`profiles_api.py`:

```python
    @router.get('/{agent_id}/config')
    def configuration(agent_id: str, response: Response):
        response.headers['Cache-Control'] = 'no-store'
        try:
            return get_agent_config(agent_id, conninfo=database().conninfo)
        except LookupError:
            raise HTTPException(404, 'Agent not found') from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @router.get('/{agent_id}/run-resources')
    def run_resources(agent_id: str, response: Response):
        """What a caller may pass to one run of a per-run agent: the Test tab picker and Generate documents."""
        response.headers['Cache-Control'] = 'no-store'
        try:
            config = get_agent_config(agent_id, conninfo=database().conninfo)
        except LookupError:
            raise HTTPException(404, 'Agent not found') from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        if not per_run(config):
            return {'resources': 'fixed'}
        with database().connect() as conn:
            return {'resources': 'per_run', **run_resource_options(conn, store.project(conn, app_name), config)}
```

`claude_code_api.py` (`create_session`): the comment becomes `# the agent's own configuration`, and
`snapshot = {'system_prompt': agent_system_prompt(config)}`.

- [ ] **Step 4: Update the existing tests**

- `test_claude_code_sessions.py`: drop `'agent_version': None` from the `prompt_snapshot` equality (around line 95);
  replace `assert (snapshot['claude_code_tools'], snapshot['agent_version']) == (['Read', 'Grep'], None)` with
  `assert snapshot['claude_code_tools'] == ['Read', 'Grep'] and 'agent_version' not in snapshot`; in
  `test_session_uses_the_active_version_tools_and_code_projects` replace the last snapshot assertion with
  `assert snapshot['claude_code_tools'] == ['Read', 'Grep'] and 'agent_version' not in snapshot`; replace
  `{'system_prompt', 'agent_version'}` with `{'system_prompt'}`.
- `test_agent_versions.py`: delete `test_drafts_activation_rollback_and_pinned_native_conversations` and
  `test_save_review_as_draft_uses_reviewed_snapshot_and_keeps_other_fields` (they choose versions per conversation
  and read `/config?version_id`; Task 6 brings the replacement for suggestions).
- `test_run_resources_test_chats.py`: delete `test_a_version_pinned_per_run_chat_drafts_with_that_versions_selection`.

- [ ] **Step 5: Run the tests**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_conversations_use_the_agent.py tests/test_claude_code_sessions.py tests/test_agent_versions.py tests/test_run_resources_test_chats.py tests/test_agent_sessions.py -q`
Expected: PASS.

Run: `cd services/admin-api && uv run --frozen pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/admin-api
git commit -m "feat(admin-api): conversations run the agent; no version per conversation, run or preview

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: 0080 makes each active version the agent, then drops versions, prompt history and version references

**Files:**
- Create: `db/migrations/0080_clone_agents.up.sql`, `db/migrations/0080_clone_agents.down.sql`
- Modify: `db/tests/run.sh` (pin `78` → `79`; the coordinator check)
- Modify: `services/admin-api/src/knowledge_admin/knowledge_agents/config.py`, `agent_sessions.py`, `profiles_api.py`,
  `api.py`
- Create: `services/admin-api/src/knowledge_admin/agent_reviews.py`; Delete: `agent_versions.py`
- Create: `services/admin-api/tests/test_clone_agents_migration.py`, `services/admin-api/tests/test_agent_reviews.py`
- Delete: `services/admin-api/tests/test_agent_versions.py`
- Modify tests: `test_agent_resources.py`, `test_agent_tools_api.py`, `test_agent_tools_config.py`,
  `test_agent_tools_migration.py`, `test_code_graphs_config.py`, `test_knowledge_agents_config.py`, `test_agents.py`,
  `test_claude_code_agents.py`, `test_claude_code_sessions.py`, `test_delete_agent_history.py`,
  `test_run_resources_test_chats.py`, `test_session_migration.py`, `test_data_root_folders_migration.py`,
  `test_remove_coordinator_migration.py`, `test_claude_code_migration.py`, `test_knowledge_agents_migration.py`,
  `test_todo.py`

**Interfaces:**
- Produces: `get_agent_config(agent_id, *, conninfo='')` (no `version_id`, `working_copy`, `config_version`);
  `config.SECTIONS: dict[str, str]`; `config.sections(config) -> dict[str, Any]`; `agents.cloned_from`;
  `agent_reviews.ServerInstructions`, `InstructionChanges`, `TuningSuggestion` (moved verbatim).
- Removed: `/api/agents/{id}/versions…`; `agent_version_id`, `config_version` in session rows and listings; the list
  overlay from the active version.

- [ ] **Step 1: Write the failing tests**

Create `services/admin-api/tests/test_clone_agents_migration.py`:

```python
"""0080: every active configuration version becomes its agent's configuration (what ran before runs after), then the
versions, the prompt history and the version references go. Down restores the empty schema."""
import copy
from pathlib import Path
from uuid import uuid4

import asyncssh
from psycopg.types.json import Jsonb

from knowledge_admin.knowledge_agents.config import CLAUDE_CODE_TOOLS, get_agent_config, sections
from knowledge_admin.tools.catalog import implied_tools
from test_agent_ssh import save
from test_agents import agent_mounts, saved_agent  # noqa: F401  (fixtures)
from test_api import _client
from test_claude_code_agents import claude_code_agent
from test_profiles import create
from test_repositories import HEADERS

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0080_clone_agents.up.sql', '0080_clone_agents.down.sql'
TABLES = ('agent_versions', 'agent_prompts', 'agent_prompt_versions', 'agent_prompt_version_details')
COLUMNS = (('agents', 'active_config_version_id'), ('agents', 'next_config_version'),
           ('agent_sessions', 'agent_version_id'), ('agents', 'cloned_from'))
AFTER = {'tables': [], 'columns': ['agents.cloned_from'], 'function': 0}
BEFORE = {'tables': list(TABLES), 'function': 1, 'columns': [
    'agent_sessions.agent_version_id', 'agents.active_config_version_id', 'agents.next_config_version']}


def run(conn, name: str):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def schema(conn) -> dict:
    present = {(row['table_name'], row['column_name']) for row in conn.execute(
        "SELECT table_name,column_name FROM information_schema.columns WHERE table_schema='ka'")}
    return {
        'tables': [t for t in TABLES if conn.execute('SELECT to_regclass(%s) AS r', (f'ka.{t}',)).fetchone()['r']],
        'columns': sorted(f'{table}.{column}' for table, column in COLUMNS if (table, column) in present),
        'function': conn.execute("""SELECT count(*) AS n FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='ka' AND p.proname='guard_prompt_version'""").fetchone()['n'],
    }


def as_saved(config: dict) -> dict:
    """The configuration as a version stored it (0034…0079): no live graphs, the prompt version's fields."""
    saved = copy.deepcopy(config)
    saved['code'].pop('graphs', None)
    saved['system_prompt'] = {'id': str(uuid4()), 'title': 'Tuned', 'version': 2, 'revision': 1,
                              'content': saved['system_prompt']['content']}
    return saved


def ran_before(saved: dict, agent: dict) -> dict:
    """What get_agent_config returned before 0080 for an agent whose active version this was."""
    config = copy.deepcopy(saved)
    config['enabled'], config['runtime'] = agent['enabled'], agent['runtime']
    config.setdefault('claude_code_tools', list(CLAUDE_CODE_TOOLS))  # versions saved before 0072
    config.setdefault('resources', 'fixed')  # versions saved before 0075
    config['tools'] = {'items': []} if agent['runtime'] != 'openai' else config.get('tools', implied_tools(config))
    return config


def activate(conn, agent_id: str, saved: dict, actor) -> dict:
    version = conn.execute('''INSERT INTO ka.agent_versions(agent,version,label,config,created_by,published_at)
        VALUES (%s,1,'Active',%s,%s,now()) RETURNING id''', (agent_id, Jsonb(saved), actor)).fetchone()['id']
    conn.execute('UPDATE ka.agents SET active_config_version_id=%s,next_config_version=2 WHERE id=%s', (version, agent_id))
    return conn.execute('SELECT enabled,runtime FROM ka.agents WHERE id=%s', (agent_id,)).fetchone()


def test_every_agent_runs_what_it_ran_before(store, saved_agent):
    client, server = saved_agent[0], saved_agent[1]
    key = asyncssh.generate_private_key('ssh-ed25519').export_private_key().decode()
    save(client, f"/api/agents/operations/servers/{server['id']}/ssh", username='agent-user', port=2222,
         known_hosts='', allow_sudo=False, private_key=key)
    working = {agent_id: sections(get_agent_config(agent_id, conninfo=store.conninfo)) for agent_id in ('search', 'code')}
    saved = as_saved(get_agent_config('operations', conninfo=store.conninfo))
    saved['system_prompt']['content'] = 'Answer from evidence only.'
    saved['instructions'] = 'Gather evidence first.'
    saved['when_to_use'] = 'When payments fail.'
    for entry in saved['ssh']['servers']:
        if entry['instructions_source'] == 'agent':
            entry['instructions'] = saved['instructions']
    saved['ssh']['servers'][0].update(instructions='Check only the queue.', instructions_source='agent_version')
    saved['services']['enabled'] = False
    for key_name in ('tools', 'resources', 'claude_code_tools'):  # saved before 0072, 0075 and 0078
        saved.pop(key_name)
    with store.connect() as conn:  # the working copy moved on after the version was activated
        conn.execute("UPDATE ka.agent_configs SET instructions='Working copy edits' WHERE agent='operations' AND server_id IS NULL")
        conn.execute("UPDATE ka.agent_server_connections SET port=2200 WHERE agent_id='operations'")
        conn.execute("DELETE FROM ka.agent_tools WHERE agent_id='operations'")
        conn.execute("UPDATE ka.agents SET services_enabled=true,when_to_use='Later' WHERE id='operations'")
        conn.commit()
        run(conn, DOWN)
        conn.commit()
        agent = activate(conn, 'operations', saved, store.actor(conn, 'migration-admin', 'migration@test'))
        conn.commit()
        run(conn, UP)
        conn.commit()
    after = get_agent_config('operations', conninfo=store.conninfo)
    assert sections(after) == sections(ran_before(saved, agent))
    assert after['system_prompt'] == {'content': 'Answer from evidence only.'}
    host = after['ssh']['servers'][0]
    assert (host['instructions'], host['instructions_source'], host['port']) == ('Check only the queue.', 'server_override', 2222)
    assert after['services']['enabled'] is False and after['when_to_use'] == 'When payments fail.'
    assert {item['tool'] for item in after['tools']['items'] if item['mode'] == 'on'} == {
        item['tool'] for item in implied_tools(saved)['items'] if item['mode'] == 'on'}
    for agent_id, before in working.items():  # agents without a version keep their own configuration
        assert sections(get_agent_config(agent_id, conninfo=store.conninfo)) == before, agent_id


def test_a_claude_code_agent_keeps_its_versions_claude_code_tools(store, tmp_path, monkeypatch):
    for variable in ('CODE_ROOT', 'DATA_ROOT_DIR'):
        monkeypatch.setenv(variable, str(tmp_path.resolve()))
    client = _client(tmp_path, repository_store=store)
    writer = claude_code_agent(client)
    saved = as_saved(get_agent_config(writer, conninfo=store.conninfo))
    saved['claude_code_tools'] = ['Read']
    saved['system_prompt']['content'] = 'Write runbooks.'
    with store.connect() as conn:
        run(conn, DOWN)
        conn.commit()
        agent = activate(conn, writer, saved, store.actor(conn, 'migration-admin', 'migration@test'))
        conn.commit()
        run(conn, UP)
        conn.commit()
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_tools WHERE agent_id=%s', (writer,)).fetchone()['n'] == 0
    after = get_agent_config(writer, conninfo=store.conninfo)
    assert sections(after) == sections(ran_before(saved, agent))
    assert (after['claude_code_tools'], after['tools'], after['system_prompt']) == (
        ['Read'], {'items': []}, {'content': 'Write runbooks.'})


def test_pinned_conversations_keep_their_configuration(store, saved_agent):
    client = saved_agent[0]
    pinned = {'id': 'operations', 'instructions': 'Pinned instructions',
              'config_version': {'id': str(uuid4()), 'version': 3, 'label': 'Old'}}
    with store.connect() as conn:
        run(conn, DOWN)
        conn.commit()
        project, actor = store.project(conn, 'shop'), store.actor(conn, 'migration-admin', 'migration@test')
        version = conn.execute('''INSERT INTO ka.agent_versions(agent,version,label,config,created_by)
            VALUES ('operations',3,'Old','{}',%s) RETURNING id''', (actor,)).fetchone()['id']
        session = conn.execute('''INSERT INTO ka.agent_sessions(id,project_id,agent,actor_id,title,config,is_test,agent_version_id)
            VALUES (%s,%s,'operations',%s,'Old chat',%s,true,%s) RETURNING id''',
            (uuid4(), project, actor, Jsonb(pinned), version)).fetchone()['id']
        conn.commit()
        run(conn, UP)
        conn.commit()
    detail = client.get(f'/api/agents/operations/sessions/{session}', headers=HEADERS)
    assert detail.status_code == 200, detail.text
    assert detail.json()['config'] == pinned and 'agent_version_id' not in detail.json()
    listed = client.get('/api/agents/operations/sessions', headers=HEADERS).json()['sessions']
    assert [row['id'] for row in listed] == [str(session)] and 'config_version' not in listed[0]


def test_up_drops_versions_and_prompt_history_and_down_restores_the_empty_schema(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    with store.connect() as conn:
        assert schema(conn) == AFTER
        run(conn, DOWN)
        conn.commit()
        assert schema(conn) == BEFORE
        for table in TABLES[:3]:
            assert conn.execute(f'SELECT count(*) AS n FROM ka.{table}').fetchone()['n'] == 0, table
        run(conn, UP)
        conn.commit()
        assert schema(conn) == AFTER
    created = create(client, 'After 0080')  # new agents get their prompt directly
    assert (created['system_prompt'], created['cloned_from']) == ('Instructions for After 0080.', None)
```

Create `services/admin-api/tests/test_agent_reviews.py` by moving three tests out of `test_agent_versions.py`
verbatim: `test_sdk_tuning_schema_decodes_nested_changes_and_server_instructions`,
`test_sdk_tuning_schema_still_rejects_invalid_encoded_changes` and
`test_invalid_tuning_output_is_corrected_once_before_saving`, with the header

```python
"""Tuning reviews: the reviewer's output shape, its correction, and (Task 6) applying a reviewed suggestion."""
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from agents import AgentOutputSchema, ModelBehaviorError

from knowledge_admin.agent_reviews import TuningSuggestion
from test_agent_runs import direct, events  # noqa: F401  (fixture)
from test_sdk_agent import responses_api  # noqa: F401  (fixture)
from test_repositories import HEADERS
```

then `git rm services/admin-api/tests/test_agent_versions.py`.

- [ ] **Step 2: Run them to verify they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_clone_agents_migration.py tests/test_agent_reviews.py -q`
Expected: FAIL: `ImportError: cannot import name 'sections'` and `No module named 'knowledge_admin.agent_reviews'`.

- [ ] **Step 3: Write the migration**

`db/migrations/0080_clone_agents.up.sql`:

```sql
BEGIN;
SET search_path = ka, public;
SET CONSTRAINTS ALL IMMEDIATE;

-- An agent has one configuration. First, what runs today becomes the agent: each agent with an active configuration
-- version gets that version's configuration, as get_agent_config read it (versions saved before 0072/0075/0078: all
-- Claude Code tools, fixed resources, 0078's implied tools). Name, description and enabled stay the agent's.
-- Selections of resources deleted or archived since are dropped, as the loader dropped them.
CREATE TEMPORARY TABLE active_versions ON COMMIT DROP AS
SELECT a.id AS agent_id, a.runtime, v.config AS c,
    CASE WHEN (SELECT count(*) FROM projects WHERE archived_at IS NULL) = 1
        THEN (SELECT id FROM projects WHERE archived_at IS NULL) END AS project_id
FROM agents a JOIN agent_versions v ON v.id = a.active_config_version_id AND v.agent = a.id;

UPDATE agents a SET
    when_to_use = coalesce(v.c->>'when_to_use', a.when_to_use),
    model_id = (SELECT m.id FROM llm_models m WHERE m.id::text = v.c->'model'->>'id'),
    claude_code_tools = CASE WHEN v.c ? 'claude_code_tools'
        THEN ARRAY(SELECT jsonb_array_elements_text(v.c->'claude_code_tools')) ELSE '{Read,Grep,Glob,Bash}' END,
    resources = coalesce(v.c->>'resources', 'fixed'),
    system_prompt = coalesce(v.c->'system_prompt'->>'content', a.system_prompt),
    documents_enabled = coalesce((v.c->'documents'->>'enabled')::boolean, false),
    documents_grounded = coalesce((v.c->'documents'->>'grounded')::boolean, false),
    all_collections = coalesce((v.c->'documents'->>'all_collections')::boolean, false),
    repositories_enabled = coalesce((v.c->'code'->>'enabled')::boolean, false),
    all_repositories = coalesce((v.c->'code'->>'all_projects')::boolean, false),
    ssh_enabled = coalesce((v.c->'ssh'->>'enabled')::boolean, false),
    skills_enabled = coalesce((v.c->'skills'->>'enabled')::boolean, false),
    mcp_enabled = coalesce((v.c->'mcp'->>'enabled')::boolean, false),
    services_enabled = coalesce((v.c->'services'->>'enabled')::boolean, false),
    revision = a.revision + 1
FROM active_versions v WHERE v.agent_id = a.id;

DELETE FROM agent_collections s USING active_versions v WHERE s.agent_id = v.agent_id;
INSERT INTO agent_collections(agent_id, collection_id)
SELECT DISTINCT v.agent_id, k.id FROM active_versions v
CROSS JOIN LATERAL jsonb_array_elements(coalesce(v.c->'documents'->'collections', '[]')) e
JOIN rag_collections k ON k.id::text = e->>'id' AND k.deleted_at IS NULL
WHERE NOT coalesce((v.c->'documents'->>'all_collections')::boolean, false);
DELETE FROM agent_repositories s USING active_versions v WHERE s.agent_id = v.agent_id;
INSERT INTO agent_repositories(agent_id, repository_id)
SELECT DISTINCT v.agent_id, r.id FROM active_versions v
CROSS JOIN LATERAL jsonb_array_elements(coalesce(v.c->'code'->'projects', '[]')) e
JOIN repositories r ON r.id::text = e->>'id' AND r.archived_at IS NULL
WHERE NOT coalesce((v.c->'code'->>'all_projects')::boolean, false);
DELETE FROM agent_services s USING active_versions v WHERE s.agent_id = v.agent_id;
INSERT INTO agent_services(agent_id, service_id)
SELECT DISTINCT v.agent_id, s.id FROM active_versions v
CROSS JOIN LATERAL jsonb_array_elements(coalesce(v.c->'services'->'items', '[]')) e
JOIN services s ON s.id::text = e->>'id';
DELETE FROM skills_agents s USING active_versions v WHERE s.agent_id = v.agent_id;
INSERT INTO skills_agents(agent_id, skill_id, project_id)
SELECT DISTINCT v.agent_id, k.id, k.project_id FROM active_versions v
CROSS JOIN LATERAL jsonb_array_elements(coalesce(v.c->'skills'->'items', '[]')) e
JOIN agent_skills k ON k.id::text = e->>'id' AND k.project_id = v.project_id;

-- The agent's instructions and MCP servers live on its settings row (server_id NULL).
INSERT INTO operations_settings(project_id, agent)
SELECT project_id, agent_id FROM active_versions WHERE project_id IS NOT NULL ON CONFLICT DO NOTHING;
INSERT INTO agent_configs(project_id, agent, instructions, inherit_skills)
SELECT project_id, agent_id, '', false FROM active_versions WHERE project_id IS NOT NULL ON CONFLICT DO NOTHING;
UPDATE agent_configs g SET instructions = coalesce(v.c->>'instructions', ''), revision = g.revision + 1
FROM active_versions v WHERE g.agent = v.agent_id AND g.project_id = v.project_id AND g.server_id IS NULL;
DELETE FROM agent_config_mcp_servers m USING agent_configs g, active_versions v
    WHERE m.config_id = g.id AND g.agent = v.agent_id AND g.server_id IS NULL;
INSERT INTO agent_config_mcp_servers(config_id, project_id, mcp_server_id)
SELECT DISTINCT g.id, g.project_id, m.id FROM active_versions v
JOIN agent_configs g ON g.agent = v.agent_id AND g.project_id = v.project_id AND g.server_id IS NULL
CROSS JOIN LATERAL jsonb_array_elements(coalesce(v.c->'mcp'->'servers', '[]')) e
JOIN mcp_servers m ON m.id::text = e->>'id' AND m.project_id = g.project_id;

-- SSH: the selected servers, per-server overrides (an 'agent_version' text becomes a server override) and the
-- agent's own connections (credentials referenced as they were).
CREATE TEMPORARY TABLE active_servers ON COMMIT DROP AS
SELECT v.agent_id, v.project_id, s.id AS server_id, e AS server
FROM active_versions v
CROSS JOIN LATERAL jsonb_array_elements(coalesce(v.c->'ssh'->'servers', '[]')) e
JOIN servers s ON s.id::text = e->>'id' AND s.project_id = v.project_id;
DELETE FROM operations_servers o USING active_versions v WHERE o.agent = v.agent_id AND v.project_id IS NOT NULL;
INSERT INTO operations_servers(project_id, agent, server_id) SELECT project_id, agent_id, server_id FROM active_servers;
DELETE FROM agent_configs g USING active_versions v WHERE g.agent = v.agent_id AND g.server_id IS NOT NULL
    AND v.project_id IS NOT NULL;
INSERT INTO agent_configs(project_id, agent, server_id, instructions, inherit_skills, inherit_mcp_servers)
SELECT project_id, agent_id, server_id,
    CASE WHEN coalesce(server->>'instructions_source', 'agent') = 'agent' THEN NULL ELSE server->>'instructions' END,
    coalesce(server->>'skills_source', 'agent') = 'agent',
    coalesce(server->>'mcp_source', 'agent') = 'agent'
FROM active_servers
WHERE coalesce(server->>'instructions_source', 'agent') <> 'agent'
   OR coalesce(server->>'skills_source', 'agent') <> 'agent'
   OR coalesce(server->>'mcp_source', 'agent') <> 'agent';
INSERT INTO agent_config_skills(config_id, project_id, skill_id)
SELECT DISTINCT g.id, g.project_id, k.id FROM active_servers a
JOIN agent_configs g ON g.agent = a.agent_id AND g.server_id = a.server_id AND NOT g.inherit_skills
CROSS JOIN LATERAL jsonb_array_elements(coalesce(a.server->'skills', '[]')) e
JOIN agent_skills k ON k.id::text = e->>'id' AND k.project_id = g.project_id;
INSERT INTO agent_config_mcp_servers(config_id, project_id, mcp_server_id)
SELECT DISTINCT g.id, g.project_id, m.id FROM active_servers a
JOIN agent_configs g ON g.agent = a.agent_id AND g.server_id = a.server_id AND NOT g.inherit_mcp_servers
CROSS JOIN LATERAL jsonb_array_elements(coalesce(a.server->'mcp_servers', '[]')) e
JOIN mcp_servers m ON m.id::text = e->>'id' AND m.project_id = g.project_id;
DELETE FROM agent_server_connections c USING active_servers a
    WHERE c.agent_id = a.agent_id AND c.server_id = a.server_id AND coalesce(a.server->>'connection_source', 'server') = 'server';
INSERT INTO agent_server_connections(agent_id, server_id, project_id, username, port, allow_sudo, known_hosts, credential_id)
SELECT a.agent_id, a.server_id, a.project_id, a.server->>'username', (a.server->>'port')::integer,
    (a.server->>'allow_sudo')::boolean, coalesce(a.server->>'known_hosts', ''),
    (SELECT k.id FROM credentials k WHERE k.id::text = a.server->'private_key_ref'->>'id'
        AND k.project_id = a.project_id AND k.kind = 'ssh_private_key')
FROM active_servers a WHERE a.server->>'connection_source' = 'agent'
ON CONFLICT (agent_id, server_id) DO UPDATE SET username = EXCLUDED.username, port = EXCLUDED.port,
    allow_sudo = EXCLUDED.allow_sudo, known_hosts = EXCLUDED.known_hosts, credential_id = EXCLUDED.credential_id,
    revision = agent_server_connections.revision + 1;

-- Tools: the version's rows; a version saved before 0078 gets 0078's rule from the (materialised) switches.
DELETE FROM agent_tools t USING active_versions v WHERE t.agent_id = v.agent_id;
INSERT INTO agent_tools(agent_id, tool, mode, usage, subagent_allowed)
SELECT v.agent_id, e->>'tool', e->>'mode', e->>'usage', coalesce((e->>'subagent_allowed')::boolean, true)
FROM active_versions v CROSS JOIN LATERAL jsonb_array_elements(v.c->'tools'->'items') e
WHERE v.runtime = 'openai' AND v.c ? 'tools';
INSERT INTO agent_tools(agent_id, tool)
SELECT a.id, t.tool FROM agents a JOIN active_versions v ON v.agent_id = a.id
CROSS JOIN LATERAL (VALUES
    ('list_files',         a.repositories_enabled OR (a.documents_enabled AND NOT a.documents_grounded)),
    ('read_file',          a.repositories_enabled OR (a.documents_enabled AND NOT a.documents_grounded)),
    ('search_files',       a.repositories_enabled OR (a.documents_enabled AND NOT a.documents_grounded)),
    ('shell_run',          a.repositories_enabled OR (a.documents_enabled AND NOT a.documents_grounded)),
    ('code_search',        a.repositories_enabled),
    ('ast_grep',           a.repositories_enabled),
    ('code_graph',         a.repositories_enabled),
    ('search_documents',   a.documents_enabled),
    ('read_document',      a.documents_enabled),
    ('report_not_covered', a.documents_enabled AND a.documents_grounded),
    ('ssh_run',            a.ssh_enabled)
) AS t(tool, offered)
WHERE v.runtime = 'openai' AND NOT v.c ? 'tools' AND t.offered;

-- Then the versions, the prompt history and every reference to them go.
SET CONSTRAINTS ALL IMMEDIATE;
ALTER TABLE agents DROP CONSTRAINT agent_active_config_version;
ALTER TABLE agents DROP COLUMN active_config_version_id, DROP COLUMN next_config_version;
ALTER TABLE agent_sessions DROP CONSTRAINT session_agent_version;
ALTER TABLE agent_sessions DROP COLUMN agent_version_id;
DROP TABLE agent_versions;
DROP VIEW agent_prompt_version_details;
ALTER TABLE agent_prompts DROP CONSTRAINT active_prompt_version_fk;
DROP TABLE agent_prompt_versions;
DROP TABLE agent_prompts;
DROP FUNCTION guard_prompt_version();

-- Experiments are made on clones.
ALTER TABLE agents ADD COLUMN cloned_from text CONSTRAINT agent_cloned_from REFERENCES agents(id) ON DELETE SET NULL,
    ADD CONSTRAINT clone_of_another_agent CHECK (cloned_from <> id);
COMMENT ON COLUMN agents.cloned_from IS 'The agent this one was cloned from; Apply copies the clone''s configuration back.';

COMMIT;
```

`db/migrations/0080_clone_agents.down.sql`:

```sql
BEGIN;
SET search_path = ka, public;
SET CONSTRAINTS ALL IMMEDIATE;

-- Restores the schema 0079 had, empty: configuration versions and prompt history are not restored, and clones lose
-- the link to their original. 0079 down then gives every agent its prompt as the active prompt version.
ALTER TABLE agents DROP COLUMN cloned_from;

CREATE TABLE agent_prompts (
    agent text PRIMARY KEY REFERENCES agents(id),
    active_version_id uuid NOT NULL,
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    updated_by uuid REFERENCES principals(id),
    updated_at timestamptz NOT NULL DEFAULT now(),
    next_version integer NOT NULL DEFAULT 1 CHECK (next_version > 0)
);
CREATE TABLE agent_prompt_versions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agent text NOT NULL REFERENCES agent_prompts(agent) DEFERRABLE INITIALLY DEFERRED,
    version integer NOT NULL CHECK (version > 0),
    title text NOT NULL CHECK (char_length(btrim(title)) BETWEEN 1 AND 100),
    content text NOT NULL CHECK (char_length(btrim(content)) BETWEEN 1 AND 40000),
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_by uuid REFERENCES principals(id),
    updated_by uuid REFERENCES principals(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    UNIQUE (agent, version),
    UNIQUE (agent, id)
);
ALTER TABLE agent_prompts ADD CONSTRAINT active_prompt_version_fk
    FOREIGN KEY (agent, active_version_id) REFERENCES agent_prompt_versions(agent, id) DEFERRABLE INITIALLY DEFERRED;
CREATE FUNCTION guard_prompt_version() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF EXISTS (SELECT 1 FROM ka.agent_prompts WHERE active_version_id = OLD.id) THEN
            RAISE EXCEPTION 'The active prompt version cannot be deleted' USING ERRCODE='23514';
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.published_at IS NOT NULL THEN
        RAISE EXCEPTION 'Published prompt content is immutable' USING ERRCODE='23514';
    END IF;
    IF ROW(NEW.id, NEW.agent, NEW.version, NEW.created_at, NEW.created_by)
        IS DISTINCT FROM ROW(OLD.id, OLD.agent, OLD.version, OLD.created_at, OLD.created_by) THEN
        RAISE EXCEPTION 'Prompt version identity is immutable' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER guard_prompt_version BEFORE UPDATE OR DELETE ON agent_prompt_versions
    FOR EACH ROW EXECUTE FUNCTION guard_prompt_version();
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON agent_prompt_versions FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON agent_prompts FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE VIEW agent_prompt_version_details AS
SELECT v.*, CASE WHEN p.active_version_id=v.id THEN 'active'
                WHEN v.published_at IS NOT NULL THEN 'archived' ELSE 'draft' END AS status
FROM agent_prompt_versions v JOIN agent_prompts p USING (agent);
COMMENT ON TABLE agent_prompts IS 'Exactly one active system prompt per supported agent; activation revision serializes concurrent edits.';
COMMENT ON COLUMN agent_prompts.next_version IS 'Next draft number per agent; deleted version numbers are never reused.';
COMMENT ON TABLE agent_prompt_versions IS 'New versions start as drafts. Published content cannot be edited. Inactive versions can be deleted.';

CREATE TABLE agent_versions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agent text NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    version integer NOT NULL CHECK (version > 0),
    label text NOT NULL CHECK (length(label) BETWEEN 1 AND 160),
    notes text NOT NULL DEFAULT '',
    config jsonb NOT NULL CHECK (jsonb_typeof(config) = 'object'),
    created_by uuid NOT NULL REFERENCES principals(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    source_review_id uuid REFERENCES agent_session_reviews(id),
    UNIQUE (agent, version),
    UNIQUE (id, agent)
);
CREATE INDEX agent_versions_created ON agent_versions(agent, created_at DESC);
ALTER TABLE agents ADD COLUMN active_config_version_id uuid,
    ADD COLUMN next_config_version integer NOT NULL DEFAULT 1 CHECK (next_config_version > 0);
ALTER TABLE agents ADD CONSTRAINT agent_active_config_version
    FOREIGN KEY (active_config_version_id, id) REFERENCES agent_versions(id, agent) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE agent_sessions ADD COLUMN agent_version_id uuid;
ALTER TABLE agent_sessions ADD CONSTRAINT session_agent_version
    FOREIGN KEY (agent_version_id, agent) REFERENCES agent_versions(id, agent);

COMMIT;
```

`db/tests/run.sh`: change the pin to `[ "$rolled_back_version" = 79 ]`, and in the `coordinator_removed` query replace
`AND NOT EXISTS (SELECT 1 FROM ka.agent_prompt_versions WHERE agent = 'coordinator')` with
`AND to_regclass('ka.agent_prompt_versions') IS NULL`.

- [ ] **Step 4: The loader reads the agent only, and `sections()`**

`knowledge_agents/config.py`:
- `AgentConfig` docstring: `"""The resolved config of one agent, the same for both types. Sessions pin this shape."""`;
  delete `config_version: NotRequired[dict[str, Any]]`.
- `get_agent_config(agent_id: str, *, conninfo: str = "") -> dict[str, object]`; docstring: add "The agent's own
  configuration: what its tabs show is what runs (0080 removed configuration versions)." Remove
  `active_config_version_id` from the agent `SELECT`, delete `selected_version = …` and the whole `if selected_version
  is not None:` branch. Drop the now-unused `from uuid import UUID`.
- Add after `require_run_resources`:

```python
SECTIONS: dict[str, str] = {
    'prompt': 'Prompt', 'instructions': 'Instructions', 'when_to_use': 'When to use', 'model': 'Model',
    'type': 'Agent type', 'resources': 'Resources', 'tools': 'Tools', 'skills': 'Skills', 'documents': 'Documents',
    'code': 'Code projects', 'ssh': 'SSH', 'mcp': 'MCP', 'services': 'Services'}


def _ids(items: Iterable[Mapping[str, Any]]) -> list[str]:
    return sorted(str(item['id']) for item in items)


def _server(entry: Mapping[str, Any]) -> dict[str, Any]:
    """A selected SSH server: only what the agent decides (inherited values are the agent's own sections)."""
    def own(source: str) -> bool:
        return entry.get(source, 'agent') != 'agent'
    agent_connection = entry.get('connection_source', 'server') == 'agent'
    return {'id': str(entry['id']),
            'instructions': entry['instructions'] if own('instructions_source') else None,
            'skills': _ids(entry['skills']) if own('skills_source') else None,
            'mcp_servers': _ids(entry['mcp_servers']) if own('mcp_source') else None,
            'connection': {'username': entry['username'], 'port': entry['port'], 'allow_sudo': entry['allow_sudo'],
                           'known_hosts': entry['known_hosts'],
                           'credential_id': (entry.get('private_key_ref') or {}).get('id')} if agent_connection else None}


def sections(config: Mapping[str, Any]) -> dict[str, Any]:
    """What an agent runs with, by section (SECTIONS): everything except its identity (id, name, description, on/off),
    revisions and live data (graphs; the details of shared servers, collections, skills). Equal sections run the
    same. Clone, Apply and the 0080 migration compare these."""
    from knowledge_admin.tools.catalog import tools_of  # the catalog imports this module

    documents, code = config['documents'], config['code']
    return {
        'prompt': config['system_prompt']['content'],
        'instructions': config['instructions'],
        'when_to_use': config['when_to_use'],
        'model': str(config['model']['id']) if config['model'] else None,
        'type': {'runtime': config['runtime'],
                 'claude_code_tools': list(config.get('claude_code_tools', CLAUDE_CODE_TOOLS))},
        'resources': config.get('resources', 'fixed'),
        'tools': [{key: entry[key] for key in ('tool', 'mode', 'usage')} for entry in tools_of(config)['items']],
        'skills': {'enabled': config['skills']['enabled'], 'items': _ids(config['skills']['items'])},
        'documents': {'enabled': documents['enabled'], 'grounded': documents.get('grounded', False),
                      'all_collections': documents.get('all_collections', False),
                      'collections': [] if documents.get('all_collections') else _ids(documents.get('collections', []))},
        'code': {'enabled': code['enabled'], 'all_projects': code.get('all_projects', False),
                 'projects': [] if code.get('all_projects') else _ids(code['projects'])},
        'ssh': {'enabled': config['ssh']['enabled'],
                'servers': sorted((_server(entry) for entry in config['ssh']['servers']), key=lambda s: s['id'])},
        'mcp': {'enabled': config['mcp']['enabled'], 'servers': _ids(config['mcp']['servers'])},
        'services': {'enabled': config['services']['enabled'], 'items': _ids(config['services']['items'])},
    }
```

- [ ] **Step 5: The versions API goes; the review models move**

Create `services/admin-api/src/knowledge_admin/agent_reviews.py` with the docstring `"""Tuning reviews of a
conversation: the reviewer's output (proposed prompt and instruction texts) and applying it."""`, the imports `import
json`, `from typing import Any`, `from uuid import UUID`, `from pydantic import BaseModel, ConfigDict, Field,
field_validator`, and the classes `ServerInstructions`, `InstructionChanges`, `TuningSuggestion` copied verbatim from
`agent_versions.py`. Then `git rm services/admin-api/src/knowledge_admin/agent_versions.py`.

`api.py`: drop `agent_versions` from the import line and delete `router.include_router(agent_versions.build_api(…))`.

`agent_sessions.py`:
- `from knowledge_admin.agent_reviews import TuningSuggestion`.
- `create_session`:

```python
    return conn.execute('''INSERT INTO ka.agent_sessions
        (id,project_id,agent,actor_id,title,config,is_test,run_resources)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *,last_activity_at+idle_timeout AS expires_at''',
        (session_id,project_id,config['id'],actor_id,title,Jsonb(config),is_test,
         Jsonb(config['run_resources']) if 'run_resources' in config else None)).fetchone()
```

- `sessions` listing: `SELECT id,title,status,is_test,created_at,last_activity_at,last_activity_at+idle_timeout AS
  expires_at FROM ka.agent_sessions …` (drop `agent_version_id` and `config->'config_version' AS config_version`).

`profiles_api.py`:
- `delete_history`: delete the `UPDATE ka.agent_versions SET source_review_id=NULL …` statement.
- `delete`: delete `conn.execute('SELECT agent FROM ka.agent_prompts WHERE agent=%s FOR UPDATE', …)`, the comment
  `# Both sides of the required-prompt relationship are deferred.` and the two `DELETE FROM ka.agent_prompts…` /
  `DELETE FROM ka.agent_prompt_versions…` statements.
- `update`: delete the `if runtime == 'claude_code':` block that reads `JOIN ka.agent_versions v ON
  v.id=a.active_config_version_id` (the agent's own model is checked just above).
- `listing` becomes:

```python
    @router.get('')
    def listing():
        with database().connect() as conn:
            rows = conn.execute('''SELECT a.*,m.name AS model_name,
                (SELECT count(*) FROM ka.agent_repositories x JOIN ka.repositories r ON r.id=x.repository_id
                    WHERE x.agent_id=a.id AND r.archived_at IS NULL)::int AS code_count,
                (SELECT count(*) FROM ka.agent_collections x JOIN ka.rag_collections c ON c.id=x.collection_id
                    WHERE x.agent_id=a.id AND c.deleted_at IS NULL)::int AS collection_count,
                (SELECT count(*) FROM ka.operations_servers x JOIN ka.servers s ON s.id=x.server_id
                    LEFT JOIN ka.agent_server_connections c ON c.server_id=s.id AND c.agent_id=x.agent
                    WHERE x.agent=a.id AND s.ssh_allowed AND CASE WHEN c.server_id IS NULL THEN s.enabled
                        ELSE c.credential_id IS NOT NULL AND c.username IS NOT NULL END)::int AS server_count,
                (SELECT count(*) FROM ka.agent_services x WHERE x.agent_id=a.id)::int AS service_count
                FROM ka.agents a LEFT JOIN ka.llm_models m ON m.id=a.model_id
                ORDER BY a.created_at,a.id''').fetchall()
            saved_tools: dict[str, list[dict]] = {}
            for tool in conn.execute('SELECT agent_id,tool,mode,usage FROM ka.agent_tools'):
                saved_tools.setdefault(tool['agent_id'], []).append(tool)
            agent_mcp: dict[str, list[dict]] = {}
            for server in conn.execute('''SELECT c.agent,m.id::text AS id,m.tools FROM ka.agent_configs c
                    JOIN ka.agent_config_mcp_servers s ON s.config_id=c.id JOIN ka.mcp_servers m ON m.id=s.mcp_server_id
                    WHERE c.server_id IS NULL AND m.enabled'''):
                agent_mcp.setdefault(server['agent'], []).append(server)
            for row in rows:
                row.pop('system_prompt')  # read with the agent, not in the list
                code_count, collection_count = row.pop('code_count'), row.pop('collection_count')
                limits = {'code': 'any' if row['all_repositories'] else code_count,
                          'documents': 'any' if row['all_collections'] else collection_count,
                          'servers': row.pop('server_count'), 'services': row.pop('service_count')}
                row['tools_summary'] = tools_summary({
                    **flags(row), 'claude_code_tools': row['claude_code_tools'],
                    'mcp': {'enabled': row['mcp_enabled'], 'servers': agent_mcp.get(row['id'], [])},
                    'tools': tool_items(saved_tools.get(row['id'], []))})
                enabled = {'code': row['repositories_enabled'], 'documents': row['documents_enabled'],
                           'servers': row['ssh_enabled'], 'services': row['services_enabled']}
                row['accepts'] = ([{'kind': kind, 'limit': limit} for kind, limit in limits.items() if enabled[kind]]
                                  if row['resources'] == 'per_run' else [])
            return {'agents': rows}
```

- [ ] **Step 6: Update the existing tests**

- `test_agent_resources.py`: delete `EMPTY_CHANGES` and `new_version`; add `LATER = '0080_clone_agents.down.sql'  #
  0075 down reads ka.agent_versions`; replace `test_down_refuses_per_run_agents_and_versions_then_up_makes_every_agent_fixed` with:

```python
def test_down_refuses_per_run_agents_then_up_makes_every_agent_fixed(store, saved_agent):
    with store.connect() as conn:
        run(conn, LATER)
        run(conn, REMOVED)
        conn.commit()
        conn.execute("UPDATE ka.agents SET resources='per_run' WHERE id='operations'")
        conn.commit()
        with pytest.raises(psycopg.errors.RaiseException, match='Per-run agents'):
            run(conn, DOWN)
        conn.rollback()
        conn.execute("UPDATE ka.agents SET resources='fixed' WHERE id='operations'")
        conn.commit()
        run(conn, DOWN)
        conn.commit()
        assert not has_column(conn)
        run(conn, UP)
        conn.commit()
        assert has_column(conn)
        assert conn.execute("SELECT resources FROM ka.agents WHERE id='operations'").fetchone()['resources'] == 'fixed'
```

  In `test_the_setting_is_saved_versioned_and_listed` (rename `test_the_setting_is_saved_and_listed`) replace the
  lines from `# The first draft also activates "Initial configuration"` through the second
  `assert listed(client)['operations']['resources'] == 'fixed'` with:

```python
    back = update(client, client.get('/api/agents/operations', headers=HEADERS).json(), resources='fixed')
    assert back.json()['resources'] == 'fixed'
    assert listed(client)['operations']['resources'] == 'fixed'  # the list shows the agent: what runs
```

  In the `accepts` test replace `new_version(client, 'Code, documents and services')` and the two lines after it with:

```python
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET documents_enabled=false,services_enabled=false WHERE id='operations'")
    assert listed(client)['operations']['accepts'] == [{'kind': 'code', 'limit': 'any'}]
```

  In `test_the_list_counts_only_servers_a_run_may_use` delete the last two lines (`new_version(client, 'No SSH')` and
  its assertion).
- `test_agent_tools_api.py`: import `listed` only; in `test_the_list_summarises_the_tools_that_run` delete the three
  lines `new_version(…)`, `put(client, 'operations', view(client)['revision'], [])`, `assert … == summary  # the active
  version runs`.
- `test_agent_tools_config.py`: delete `test_versions_keep_the_tools` and
  `test_versions_and_pinned_configs_without_tools_get_the_implied_tools` and the `new_version` import; the docstring
  becomes `"""The configuration carries the agent's tools; Claude Code agents have none."""`.
- `test_agent_tools_migration.py` (`add_agent`): delete the `version = uuid4()` line and the two prompt `INSERT`s.
- `test_code_graphs_config.py`: delete `test_versions_get_the_current_graphs` and `test_versions_never_store_graphs`
  and the `from test_agent_resources import EMPTY_CHANGES, new_version` import.
- `test_knowledge_agents_config.py`: delete `test_a_version_keeps_its_tools_and_follows_the_agent_type` and
  `EMPTY_CHANGES`.
- `test_agents.py`: delete `test_version_captures_all_resources_and_updates_only_inherited_server_instructions`
  (Task 6 pins inherited server instructions for suggestions).
- `test_claude_code_agents.py`: in `test_claude_code_agents_are_configured_like_native_agents` replace everything from
  `source = client.get(f'/api/agents/{agent_id}/versions/source'…` to the end of the test with:

```python
    config = client.get(f'/api/agents/{agent_id}/config', headers=HEADERS).json()
    assert (config['runtime'], config['claude_code_tools'], config['instructions']) == ('claude_code', ['Read', 'Bash'], 'Write for operators.')
    assert [s['id'] for s in config['ssh']['servers']] == [server['id']]
```

  delete `test_the_version_model_must_fit_the_agent_type`; in
  `test_claude_code_agent_without_sessions_is_deleted_with_its_prompt` (rename `…_is_deleted`) replace the
  `agent_prompt_versions` count with `assert conn.execute('SELECT count(*) AS n FROM ka.agents WHERE id=%s',
  (agent_id,)).fetchone()['n'] == 0`.
- `test_claude_code_sessions.py`: rename `test_session_uses_the_active_version_tools_and_code_projects` to
  `test_session_uses_the_agents_tools_and_code_projects` and replace its body up to `response = client.post(…)` with:

```python
    from test_profiles import update
    client, data = sessions.client, sessions.data
    agent = claude_code_agent(client, 'Configured writer', model_id=sessions.claude, repositories_enabled=True,
                              repository_ids=[sessions.repo], claude_code_tools=['Bash'])
    profile = client.get(f'/api/agents/{agent}', headers=HEADERS).json()
    changed = update(client, profile, claude_code_tools=['Grep', 'Read'])  # what the tabs show is what runs
    assert changed.status_code == 200, changed.text
    body = {**sessions.body, 'code_project_ids': [], 'environment_ids': [], 'service_ids': []}
```

  keeping the rest of the test (the snapshot asserts `['Read', 'Grep']` and no `agent_version`).
- `test_delete_agent_history.py` (`add_history`): delete the comment and the `INSERT INTO ka.agent_versions…`
  statement; in `test_delete_with_history_removes_only_this_agents_history` delete the `ka.agent_versions` assertion.
- `test_run_resources_test_chats.py`: delete `test_a_suggestion_from_a_per_run_chat_drafts_the_whole_configuration`,
  `review_of`, `test_a_versionless_per_run_chat_drafts_from_its_snapshot_with_the_working_selection` and `from
  test_agent_versions import draft` (Task 6 pins per-run suggestions).
- `test_session_migration.py`: before `conn.execute(down,prepare=False)` add
  `conn.execute((repo/'db/migrations/0080_clone_agents.down.sql').read_text(),prepare=False)  # restores agent_versions`.
- `test_data_root_folders_migration.py` (`at_0066`): before `run(conn, DOWN)` add `run(conn, '0080_clone_agents.down.sql')
  # restores ka.agent_versions`.
- `test_remove_coordinator_migration.py`: add `LATER = ('0080_clone_agents.down.sql', '0079_agent_system_prompt.down.sql')
  # 0077 reads the prompt tables and versions` and, in every test, before its first `run(conn, DOWN)`, add `for name
  in LATER: run(conn, name)`.
- `test_claude_code_migration.py`, `test_knowledge_agents_migration.py`: `LATER = ('0080_clone_agents.down.sql',
  '0079_agent_system_prompt.down.sql')`.
- `test_todo.py` (`test_agent_delete_preserves_prompt_invariants_and_other_agents`, rename
  `test_agent_delete_keeps_other_agents`): delete the two `ka.agent_prompts` / `ka.agent_prompt_versions` asserts.

- [ ] **Step 7: Run the tests**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_clone_agents_migration.py tests/test_agent_reviews.py tests/test_agent_system_prompt_migration.py tests/test_agent_resources.py tests/test_remove_coordinator_migration.py tests/test_claude_code_migration.py tests/test_knowledge_agents_migration.py tests/test_session_migration.py tests/test_data_root_folders_migration.py tests/test_documents.py -q`
Expected: PASS.

Run: `bash db/tests/run.sh`
Expected: every `PASS:` line, exit 0.

Run: `cd services/admin-api && uv run --frozen pytest -q`
Expected: PASS.

Run: `git grep -n -E "agent_versions|agent_prompt_versions|ka\.agent_prompts|active_config_version_id|agent_version_id|config_version|working_copy" -- services packages ':!*.sql'`
Expected: only the migration tests (`test_clone_agents_migration.py`, `test_agent_system_prompt_migration.py`,
`test_remove_coordinator_migration.py`, `test_claude_code_migration.py`, `test_knowledge_agents_migration.py`,
`test_session_migration.py`, `test_data_root_folders_migration.py`, `test_agent_resources.py`).

- [ ] **Step 8: Commit**

```bash
git add db services/admin-api
git commit -m "feat: an agent has one configuration (0080)

0080 makes every active configuration version its agent's configuration (what ran before runs after), then drops
ka.agent_versions, ka.agent_prompts, ka.agent_prompt_versions, the version references on agents and sessions and
their constraints, trigger, view and function, and adds agents.cloned_from. The loader, sessions and the list read
the agent only; the versions API goes; the tuning reviewer's models move to agent_reviews.py.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Clone agent

**Files:**
- Create: `services/admin-api/src/knowledge_admin/agent_clones.py`
- Modify: `services/admin-api/src/knowledge_admin/api.py`, `profiles_api.py`
- Create: `services/admin-api/tests/test_agent_clones.py`

**Interfaces:**
- Produces: `agent_clones.clone_name(conn, name) -> str`; `agent_clones.copy_configuration(conn, source_id, target_id,
  actor_id) -> None`; `agent_clones.clone(conn, source_id, actor_id) -> {'id', 'name'}` (raises `LookupError`);
  `POST /api/agents/{id}/clone` → 201 `{id, name}`; `GET /api/agents/{id}` gains `original: {id, name} | null`;
  `GET /api/agents` rows gain `original_name`.

- [ ] **Step 1: Write the failing tests**

Create `services/admin-api/tests/test_agent_clones.py`:

```python
"""Clone agent: a new agent with an identical configuration. Saved credentials are referenced, history is not
copied, and the clone outlives its original."""
import copy
from uuid import UUID, uuid4

import asyncssh

from knowledge_admin.agent_clones import clone_name
from knowledge_admin.knowledge_agents.config import get_agent_config, sections
from knowledge_admin.profiles_api import agent_history
from test_agent_ssh import save
from test_agents import agent_mounts, saved_agent  # noqa: F401  (fixtures)
from test_repositories import HEADERS

NO_HISTORY = {'test_chats': 0, 'chats': 0, 'claude_code_sessions': 0, 'runs': 0}


def comparable(config: dict) -> dict:
    """The whole configuration without the agent's identity and the revisions of its own rows."""
    config = copy.deepcopy(config)
    for key in ('id', 'name', 'description', 'revision', 'settings_revision'):
        config.pop(key)
    for server in config['ssh']['servers']:
        server.pop('config_revision')
        server.pop('connection_revision')
    return config


def clone(client, agent_id: str = 'operations') -> dict:
    response = client.post(f'/api/agents/{agent_id}/clone', headers=HEADERS)
    assert response.status_code == 201, response.text
    return response.json()


def own_connection(client, server) -> None:
    """An SSH key saved for this agent only (a credential the clone must reference, not copy)."""
    key = asyncssh.generate_private_key('ssh-ed25519').export_private_key().decode()
    save(client, f"/api/agents/operations/servers/{server['id']}/ssh", username='agent-user', port=2222,
         known_hosts='', allow_sudo=False, private_key=key)


def test_a_clone_runs_exactly_what_its_original_runs(store, saved_agent):
    client, server = saved_agent[0], saved_agent[1]
    own_connection(client, server)
    with store.connect() as conn:
        conn.execute("""INSERT INTO ka.agent_configs(project_id,agent,server_id,instructions,inherit_skills,inherit_mcp_servers)
            SELECT project_id,'operations',id,'Check only the queue.',false,true FROM ka.servers WHERE id=%s""",
            (UUID(server['id']),))
        conn.execute("""INSERT INTO ka.agent_tools(agent_id,tool,usage) VALUES ('operations','read_file','Read narrowly.')
            ON CONFLICT (agent_id,tool) DO UPDATE SET usage=EXCLUDED.usage""")
        conn.execute("UPDATE ka.agents SET system_prompt='Answer from evidence.',resources='per_run' WHERE id='operations'")
        credentials = conn.execute('SELECT count(*) AS n FROM ka.credentials').fetchone()['n']
    original = get_agent_config('operations', conninfo=store.conninfo)
    copied = get_agent_config(clone(client)['id'], conninfo=store.conninfo)
    assert comparable(copied) == comparable(original)
    assert sections(copied) == sections(original)
    assert copied['ssh']['servers'][0]['private_key_ref'] == original['ssh']['servers'][0]['private_key_ref']
    with store.connect() as conn:  # the saved key is referenced, not duplicated
        assert conn.execute('SELECT count(*) AS n FROM ka.credentials').fetchone()['n'] == credentials


def test_a_clone_is_named_after_its_original_and_keeps_its_switch(store, saved_agent):
    client = saved_agent[0]
    original = client.get('/api/agents/operations', headers=HEADERS).json()
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET enabled=false WHERE id='operations'")
    first, second = clone(client), clone(client)
    assert (first['name'], second['name']) == (f"{original['name']} (copy)", f"{original['name']} (copy 2)")
    detail = client.get(f"/api/agents/{first['id']}", headers=HEADERS).json()
    assert (detail['enabled'], detail['cloned_from'], detail['description']) == (False, 'operations', original['description'])
    assert detail['original'] == {'id': 'operations', 'name': original['name']}
    listed = {row['id']: row for row in client.get('/api/agents', headers=HEADERS).json()['agents']}
    assert listed[first['id']]['original_name'] == original['name'] and listed['operations']['original_name'] is None
    assert client.post('/api/agents/missing/clone', headers=HEADERS).status_code == 404
    assert client.post('/api/agents/operations/clone').status_code == 403
    with store.connect() as conn:
        audit = conn.execute("SELECT details FROM ka.audit_events WHERE action='agent.cloned' ORDER BY created_at").fetchall()
    assert [row['details'] for row in audit] == [{'agent_id': first['id'], 'cloned_from': 'operations'},
                                                {'agent_id': second['id'], 'cloned_from': 'operations'}]


def test_clone_names_stay_within_the_name_limit(store):
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET name=%s WHERE id='search'", ('x' * 100,))
        assert clone_name(conn, 'x' * 100) == 'x' * 93 + ' (copy)'
        conn.execute("UPDATE ka.agents SET name=%s WHERE id='code'", ('x' * 93 + ' (copy)',))
        assert clone_name(conn, 'x' * 100) == 'x' * 91 + ' (copy 2)'


def test_history_stays_with_the_original_and_the_clone_outlives_it(store, saved_agent):
    client, server = saved_agent[0], saved_agent[1]
    own_connection(client, server)
    with store.connect() as conn:
        project, actor = store.project(conn, 'shop'), store.actor(conn, 'admin', 'admin@test')
        session = conn.execute('''INSERT INTO ka.agent_sessions(id,project_id,agent,actor_id,title,config,is_test)
            VALUES (%s,%s,'operations',%s,'Chat','{}',true) RETURNING id''', (uuid4(), project, actor)).fetchone()['id']
        conn.execute('''INSERT INTO ka.operations_runs(session_id,project_id,agent,actor_id,task,context,status)
            VALUES (%s,%s,'operations',%s,'Check disk','{}','completed')''', (session, project, actor))
    created = clone(client)
    with store.connect() as conn:
        assert agent_history(conn, created['id']) == NO_HISTORY
        credential = conn.execute('SELECT credential_id FROM ka.agent_server_connections WHERE agent_id=%s',
                                  (created['id'],)).fetchone()['credential_id']
    revision = client.get('/api/agents/operations', headers=HEADERS).json()['revision']
    deleted = client.request('DELETE', '/api/agents/operations', headers=HEADERS,
                             json={'revision': revision, 'confirmed': True, 'with_history': True})
    assert deleted.status_code == 204, deleted.text
    detail = client.get(f"/api/agents/{created['id']}", headers=HEADERS).json()
    assert (detail['cloned_from'], detail['original']) == (None, None)
    with store.connect() as conn:  # the clone still uses the saved key
        assert conn.execute('SELECT 1 FROM ka.credentials WHERE id=%s', (credential,)).fetchone() is not None
    host = get_agent_config(created['id'], conninfo=store.conninfo)['ssh']['servers'][0]
    assert host['private_key_ref']['id'] == str(credential)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_agent_clones.py -q`
Expected: FAIL: `No module named 'knowledge_admin.agent_clones'`.

- [ ] **Step 3: Implement `agent_clones.py`**

Create `services/admin-api/src/knowledge_admin/agent_clones.py`:

```python
"""Clone an agent; apply a clone's configuration back to its original. An agent has one configuration (0080)."""
from uuid import uuid4

import psycopg
from fastapi import APIRouter, HTTPException, Request
from psycopg.types.json import Jsonb

from knowledge_admin.access import request_actor

NAME_TAKEN = 'An agent with this name already exists.'
NAME_LIMIT = 100
# Everything an agent runs with on ka.agents. Name, description, enabled and cloned_from belong to the agent itself.
CONFIGURATION = ('when_to_use', 'model_id', 'runtime', 'claude_code_tools', 'resources', 'system_prompt',
                 'documents_enabled', 'documents_grounded', 'repositories_enabled', 'ssh_enabled', 'skills_enabled',
                 'mcp_enabled', 'services_enabled', 'all_collections', 'all_repositories')
SELECTIONS = (('agent_collections', 'collection_id'), ('agent_repositories', 'repository_id'),
              ('agent_services', 'service_id'))


def clone_name(conn, name: str) -> str:
    """"<name> (copy)", then "(copy 2)", "(copy 3)" … while taken; shortened to the 100-character name limit."""
    taken = {row['name'] for row in conn.execute('SELECT name FROM ka.agents')}
    number = 1
    while True:
        suffix = ' (copy)' if number == 1 else f' (copy {number})'
        candidate = name[:NAME_LIMIT - len(suffix)].rstrip() + suffix
        if candidate not in taken:
            return candidate
        number += 1


def copy_configuration(conn, source_id: str, target_id: str, actor_id) -> None:
    """Make the target run exactly what the source runs: every configuration column, selection, settings row,
    per-server override, agent SSH connection and tool. Saved credentials are referenced, never duplicated. Bumps the
    target's revisions."""
    columns = ','.join(f'{column}=s.{column}' for column in CONFIGURATION)
    conn.execute(f'''UPDATE ka.agents t SET {columns},revision=t.revision+1,updated_by=%s
        FROM ka.agents s WHERE s.id=%s AND t.id=%s''', (actor_id, source_id, target_id))
    for table, column in SELECTIONS:
        conn.execute(f'DELETE FROM ka.{table} WHERE agent_id=%s', (target_id,))
        conn.execute(f'INSERT INTO ka.{table}(agent_id,{column}) SELECT %s,{column} FROM ka.{table} WHERE agent_id=%s',
                     (target_id, source_id))
    conn.execute('DELETE FROM ka.skills_agents WHERE agent_id=%s', (target_id,))
    conn.execute('''INSERT INTO ka.skills_agents(agent_id,skill_id,project_id)
        SELECT %s,skill_id,project_id FROM ka.skills_agents WHERE agent_id=%s''', (target_id, source_id))
    conn.execute('DELETE FROM ka.agent_tools WHERE agent_id=%s', (target_id,))
    conn.execute('''INSERT INTO ka.agent_tools(agent_id,tool,mode,usage,subagent_allowed)
        SELECT %s,tool,mode,usage,subagent_allowed FROM ka.agent_tools WHERE agent_id=%s''', (target_id, source_id))
    conn.execute('DELETE FROM ka.agent_configs WHERE agent=%s', (target_id,))  # their skill and MCP rows cascade
    conn.execute('DELETE FROM ka.operations_servers WHERE agent=%s', (target_id,))
    conn.execute('DELETE FROM ka.agent_server_connections WHERE agent_id=%s', (target_id,))
    conn.execute('''INSERT INTO ka.operations_settings(project_id,agent,instructions,updated_by)
        SELECT project_id,%s,instructions,%s FROM ka.operations_settings WHERE agent=%s
        ON CONFLICT (project_id,agent) DO UPDATE SET instructions=EXCLUDED.instructions,
            revision=ka.operations_settings.revision+1,updated_by=EXCLUDED.updated_by''', (target_id, actor_id, source_id))
    conn.execute('''INSERT INTO ka.operations_servers(project_id,agent,server_id)
        SELECT project_id,%s,server_id FROM ka.operations_servers WHERE agent=%s''', (target_id, source_id))
    conn.execute('''INSERT INTO ka.agent_server_connections(agent_id,server_id,project_id,username,port,allow_sudo,
            known_hosts,credential_id,credential_kind,updated_by)
        SELECT %s,server_id,project_id,username,port,allow_sudo,known_hosts,credential_id,credential_kind,%s
        FROM ka.agent_server_connections WHERE agent_id=%s''', (target_id, actor_id, source_id))
    for row in conn.execute('''SELECT id,project_id,server_id,instructions,inherit_skills,inherit_mcp_servers
            FROM ka.agent_configs WHERE agent=%s''', (source_id,)).fetchall():
        copied = conn.execute('''INSERT INTO ka.agent_configs(project_id,agent,server_id,instructions,inherit_skills,
                inherit_mcp_servers,updated_by) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id''',
            (row['project_id'], target_id, row['server_id'], row['instructions'], row['inherit_skills'],
             row['inherit_mcp_servers'], actor_id)).fetchone()['id']
        for table, column in (('agent_config_skills', 'skill_id'), ('agent_config_mcp_servers', 'mcp_server_id')):
            conn.execute(f'''INSERT INTO ka.{table}(config_id,project_id,{column})
                SELECT %s,project_id,{column} FROM ka.{table} WHERE config_id=%s''', (copied, row['id']))


def clone(conn, source_id: str, actor_id) -> dict[str, str]:
    """A new agent with the source's configuration and on/off switch, named after it. History is not copied."""
    source = conn.execute('SELECT name,description,enabled FROM ka.agents WHERE id=%s FOR UPDATE',
                          (source_id,)).fetchone()
    if source is None:
        raise LookupError('Agent not found')
    created = {'id': str(uuid4()), 'name': clone_name(conn, source['name'])}
    conn.execute('''INSERT INTO ka.agents(id,name,description,enabled,cloned_from,created_by,updated_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s)''',
        (created['id'], created['name'], source['description'], source['enabled'], source_id, actor_id, actor_id))
    copy_configuration(conn, source_id, created['id'], actor_id)
    return created


def build_api(app_name, store):
    router = APIRouter(prefix='/agents/{agent_id}')

    def database():
        if store is None:
            raise HTTPException(503, 'Agent storage is not configured.')
        return store

    def audit(conn, actor, action: str, details: dict) -> None:
        conn.execute('''INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,details)
            VALUES (%s,%s,%s,'agent',%s)''', (store.project(conn, app_name), actor, action, Jsonb(details)))

    @router.post('/clone', status_code=201)
    def clone_agent(agent_id: str, request: Request):
        try:
            with database().connect() as conn:
                actor = store.actor(conn, *request_actor(request))
                try:
                    created = clone(conn, agent_id, actor)
                except LookupError:
                    raise HTTPException(404, 'Agent not found') from None
                audit(conn, actor, 'agent.cloned', {'agent_id': created['id'], 'cloned_from': agent_id})
                return created
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, NAME_TAKEN) from None

    return router
```

`api.py`: in the late import line add `agent_clones` (`from knowledge_admin import llm_api, mcp_api, profiles_api,
agent_wizard, tools_api, agent_clones`) and `router.include_router(agent_clones.build_api(app_name,
repository_store))` after `profiles_api`.

`profiles_api.py`:
- `read()`: after `row['skill_ids'] = …` add
  `row['original'] = conn.execute('SELECT id,name FROM ka.agents WHERE id=%s', (row['cloned_from'],)).fetchone() if row['cloned_from'] else None`.
- `listing()`: select `o.name AS original_name` and join `LEFT JOIN ka.agents o ON o.id=a.cloned_from` (after the
  `llm_models` join).

- [ ] **Step 4: Run the tests**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_agent_clones.py tests/test_profiles.py tests/test_delete_agent_history.py -q`
Expected: PASS.

Run: `cd services/admin-api && uv run --frozen pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api
git commit -m "feat(admin-api): Clone agent

POST /api/agents/{id}/clone makes \"<name> (copy)\" with an identical configuration (credentials referenced, history
not copied, the on/off switch kept); the profile and the list say what an agent is a clone of.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Apply to original

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/agent_clones.py`,
  `services/admin-api/src/knowledge_admin/knowledge_agents/config.py`
- Create: `services/admin-api/tests/test_agent_apply.py`

**Interfaces:**
- Produces: `config.fingerprint(config) -> str` (sha256 of `sections`); `config.changed_sections(source, target) ->
  list[str]` (labels in `SECTIONS` order); `GET /api/agents/{id}/apply` → `{original: {id, name, revision},
  fingerprint, changes: [label, …]}`; `POST /api/agents/{id}/apply` `{revision, fingerprint}` → `{id, revision}` of the
  original; audit `agent.config.applied` `{agent_id: <original>, clone_id}`; texts `NOT_A_CLONE`, `APPLY_WORKING`,
  `ORIGINAL_CHANGED`.

- [ ] **Step 1: Write the failing tests**

Create `services/admin-api/tests/test_agent_apply.py`:

```python
"""Apply to original: the clone's configuration replaces the original's, except name and description; refused for a
stale dialog, while the original works, and when there is no original."""
from uuid import uuid4

from knowledge_admin.agent_clones import APPLY_WORKING, NOT_A_CLONE, ORIGINAL_CHANGED
from knowledge_admin.knowledge_agents.config import get_agent_config, sections
from test_agent_clones import clone
from test_agents import agent_mounts, saved_agent  # noqa: F401  (fixtures)
from test_repositories import HEADERS


def preview(client, agent_id: str) -> dict:
    response = client.get(f'/api/agents/{agent_id}/apply', headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


def apply(client, agent_id: str, value: dict):
    return client.post(f'/api/agents/{agent_id}/apply', headers=HEADERS,
                       json={'revision': value['original']['revision'], 'fingerprint': value['fingerprint']})


def edit_prompt(client, agent_id: str, text: str) -> None:
    revision = client.get(f'/api/agents/{agent_id}', headers=HEADERS).json()['revision']
    saved = client.put(f'/api/agents/{agent_id}/prompt', headers=HEADERS, json={'revision': revision, 'system_prompt': text})
    assert saved.status_code == 200, saved.text


def test_apply_copies_everything_but_name_and_description(store, saved_agent):
    client = saved_agent[0]
    copy_id = clone(client)['id']
    original = client.get('/api/agents/operations', headers=HEADERS).json()
    edit_prompt(client, copy_id, 'Answer from evidence.')
    with store.connect() as conn:
        conn.execute("UPDATE ka.agent_configs SET instructions='Gather evidence first.' WHERE agent=%s AND server_id IS NULL",
                     (copy_id,))
        conn.execute('DELETE FROM ka.skills_agents WHERE agent_id=%s', (copy_id,))
        conn.execute("""UPDATE ka.agents SET name='Experiment',description='Evidence first',when_to_use='Payments',
            enabled=false WHERE id=%s""", (copy_id,))
    value = preview(client, copy_id)
    assert value['original'] == {'id': 'operations', 'name': original['name'], 'revision': original['revision']}
    assert value['changes'] == ['Prompt', 'Instructions', 'When to use', 'Skills']
    applied = apply(client, copy_id, value)
    assert applied.status_code == 200, applied.text
    assert applied.json() == {'id': 'operations', 'revision': original['revision'] + 1}
    assert sections(get_agent_config('operations', conninfo=store.conninfo)) == \
        sections(get_agent_config(copy_id, conninfo=store.conninfo))
    after = client.get('/api/agents/operations', headers=HEADERS).json()
    assert (after['name'], after['description'], after['enabled'], after['cloned_from']) == (
        original['name'], original['description'], original['enabled'], None)
    assert preview(client, copy_id)['changes'] == []
    with store.connect() as conn:
        audit = conn.execute("SELECT details FROM ka.audit_events WHERE action='agent.config.applied'").fetchall()
    assert [row['details'] for row in audit] == [{'agent_id': 'operations', 'clone_id': copy_id}]


def test_apply_is_refused_when_the_original_changed_or_is_working(store, saved_agent):
    client, model = saved_agent[0], saved_agent[6]
    copy_id = clone(client)['id']
    edit_prompt(client, copy_id, 'Answer from evidence.')
    value = preview(client, copy_id)
    with store.connect() as conn:  # an instructions save keeps agents.revision but changes what runs
        conn.execute("UPDATE ka.agent_configs SET instructions='Edited meanwhile.' WHERE agent='operations' AND server_id IS NULL")
    stale = apply(client, copy_id, value)
    assert stale.status_code == 409 and stale.json()['error']['message'] == ORIGINAL_CHANGED
    value = preview(client, copy_id)
    edit_prompt(client, 'operations', 'Newer.')
    assert apply(client, copy_id, value).json()['error']['message'] == ORIGINAL_CHANGED
    value = preview(client, copy_id)
    session = uuid4()
    with store.connect() as conn:  # a documentation session of the original is working
        conn.execute('''INSERT INTO ka.agent_sessions_cc(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,
            context_snapshot,document_name,folder,status) SELECT %s,p.id,'operations',(SELECT id FROM ka.principals LIMIT 1),
            %s,'Payments','{}','{}','Payments','payments','working' FROM ka.projects p WHERE p.archived_at IS NULL''',
            (session, model['id']))
    working = apply(client, copy_id, value)
    assert working.status_code == 409 and working.json()['error']['message'] == APPLY_WORKING
    with store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions_cc SET status='stopped' WHERE id=%s", (session,))
    assert apply(client, copy_id, value).status_code == 200


def test_apply_needs_a_clone_whose_original_still_exists(store, saved_agent):
    client = saved_agent[0]
    copy_id = clone(client)['id']
    plain = client.get('/api/agents/operations/apply', headers=HEADERS)
    assert plain.status_code == 404 and plain.json()['error']['message'] == NOT_A_CLONE
    assert client.get(f'/api/agents/{copy_id}/apply').status_code == 403
    revision = client.get('/api/agents/operations', headers=HEADERS).json()['revision']
    assert client.request('DELETE', '/api/agents/operations', headers=HEADERS,
                          json={'revision': revision, 'confirmed': True, 'with_history': True}).status_code == 204
    assert client.get(f'/api/agents/{copy_id}/apply', headers=HEADERS).status_code == 404
    gone = client.post(f'/api/agents/{copy_id}/apply', headers=HEADERS, json={'revision': 1, 'fingerprint': '0' * 64})
    assert gone.status_code == 404 and gone.json()['error']['message'] == NOT_A_CLONE
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_agent_apply.py -q`
Expected: FAIL: `ImportError: cannot import name 'APPLY_WORKING'`.

- [ ] **Step 3: Implement**

`knowledge_agents/config.py`: add `import hashlib` and `import json`; after `sections`:

```python
def fingerprint(config: Mapping[str, Any]) -> str:
    """Changes whenever what the agent runs with changes, whichever tab saved it (instructions and SSH saves do not
    bump agents.revision)."""
    return hashlib.sha256(json.dumps(sections(config), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def changed_sections(source: Mapping[str, Any], target: Mapping[str, Any]) -> list[str]:
    """The labels of the sections that differ, in SECTIONS order."""
    ours, theirs = sections(source), sections(target)
    return [label for key, label in SECTIONS.items() if ours[key] != theirs[key]]
```

`agent_clones.py`: add imports `from fastapi import Response`, `from pydantic import BaseModel, ConfigDict, Field`,
`from knowledge_admin.knowledge_agents.config import changed_sections, fingerprint, get_agent_config`, `from
knowledge_admin.profiles_api import WORKING`; add the texts and input:

```python
NOT_A_CLONE = 'This agent is not a clone.'
APPLY_WORKING = ('The original agent is working right now. Wait for its documentation sessions and runs to finish, '
                 'then apply.')
ORIGINAL_CHANGED = 'The original changed; review again.'


class ApplyInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1, strict=True)
    fingerprint: str = Field(min_length=64, max_length=64)
```

and inside `build_api`, before `return router`:

```python
    def config(agent_id: str) -> dict:
        try:
            return get_agent_config(agent_id, conninfo=store.conninfo)
        except LookupError:
            raise HTTPException(404, 'Agent not found') from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    def original_of(conn, agent_id: str) -> dict:
        row = conn.execute('SELECT cloned_from FROM ka.agents WHERE id=%s', (agent_id,)).fetchone()
        if row is None:
            raise HTTPException(404, 'Agent not found')
        if row['cloned_from'] is None:  # never a clone, or its original was deleted
            raise HTTPException(404, NOT_A_CLONE)
        return conn.execute('SELECT id,name,revision FROM ka.agents WHERE id=%s', (row['cloned_from'],)).fetchone()

    @router.get('/apply')
    def preview(agent_id: str, response: Response):
        """What Apply would replace on the original, and the fingerprint the dialog sends back."""
        response.headers['Cache-Control'] = 'no-store'
        with database().connect() as conn:
            original = original_of(conn, agent_id)
        target = config(original['id'])
        return {'original': original, 'fingerprint': fingerprint(target),
                'changes': changed_sections(config(agent_id), target)}

    @router.post('/apply')
    def apply(agent_id: str, value: ApplyInput, request: Request):
        """Copy the clone's configuration onto its original in one transaction (not its name, description, on/off
        switch or cloned_from)."""
        with database().connect() as conn:
            original = original_of(conn, agent_id)
            for row_id in sorted((agent_id, original['id'])):
                conn.execute('SELECT id FROM ka.agents WHERE id=%s FOR UPDATE', (row_id,))
            original = original_of(conn, agent_id)  # again, under the locks
            if conn.execute(WORKING, {'a': original['id']}).fetchone():
                raise HTTPException(409, APPLY_WORKING)
            if original['revision'] != value.revision or fingerprint(config(original['id'])) != value.fingerprint:
                raise HTTPException(409, ORIGINAL_CHANGED)
            actor = store.actor(conn, *request_actor(request))
            copy_configuration(conn, agent_id, original['id'], actor)
            audit(conn, actor, 'agent.config.applied', {'agent_id': original['id'], 'clone_id': agent_id})
            return conn.execute('SELECT id,revision FROM ka.agents WHERE id=%s', (original['id'],)).fetchone()
```

- [ ] **Step 4: Run the tests**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_agent_apply.py tests/test_agent_clones.py -q`
Expected: PASS.

Run: `cd services/admin-api && uv run --frozen pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api
git commit -m "feat(admin-api): Apply a clone to its original

GET /api/agents/{id}/apply lists the sections that would change; POST copies the clone's configuration onto the
original (not its name, description or on/off switch), checked by revision and fingerprint, refused while the original
works; audited as agent.config.applied.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Feedback suggestions apply to the agent or to a new clone

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/agent_reviews.py`, `api.py`
- Modify: `services/admin-api/tests/test_agent_reviews.py`

**Interfaces:**
- Consumes: `agent_clones.clone`, `config.fingerprint`, `get_agent_config`.
- Produces: `agent_reviews.write_changes(conn, project_id, agent_id, changes, actor_id)`; `GET
  /api/agents/{id}/reviews/{review_id}` → `{suggestion, changes, revision, fingerprint, system_prompt, instructions,
  servers: [{id, name, instructions, inherits}]}`; `POST …/apply` `{target: 'agent'|'clone', revision, fingerprint,
  changes}` → `{agent_id}`; audit `agent.suggestion.applied` `{agent_id, review_id, target}`.

- [ ] **Step 1: Write the failing tests**

Append to `services/admin-api/tests/test_agent_reviews.py` (add imports `from psycopg.types.json import Jsonb`,
`from knowledge_admin.agent_reviews import AGENT_CHANGED, PROMPT_REQUIRED, UNKNOWN_SERVER`, `from
knowledge_admin.knowledge_agents.config import get_agent_config, sections`, `from test_agents import agent_mounts,
saved_agent  # noqa: F401`):

```python
NARROWED = {'run_resources': {'code_project_ids': []}, 'code': {'enabled': True, 'all_projects': False, 'projects': []}}


def saved_review(store, agent_id: str, changes: dict, agent_config: dict | None = None) -> str:
    """A tuning suggestion saved for one of the agent's conversations."""
    with store.connect() as conn:
        project, actor = store.project(conn, 'shop'), store.actor(conn, 'reviewer', 'reviewer@test')
        session = conn.execute('''INSERT INTO ka.agent_sessions(id,project_id,agent,actor_id,title,config,is_test)
            VALUES (%s,%s,%s,%s,'Chat','{}',true) RETURNING id''', (uuid4(), project, agent_id, actor)).fetchone()['id']
        return str(conn.execute('''INSERT INTO ka.agent_session_reviews(session_id,actor_id,comment,context,suggestion)
            VALUES (%s,%s,'Feedback',%s,'Be clearer.') RETURNING id''',
            (session, actor, Jsonb({'agent_config': agent_config or {}, 'changes': changes}))).fetchone()['id'])


def changes(system_prompt=None, instructions=None, servers=()) -> dict:
    return {'system_prompt': system_prompt, 'instructions': instructions, 'server_instructions': list(servers)}


def apply_review(client, agent_id, review, body, target='agent', **change):
    return client.post(f'/api/agents/{agent_id}/reviews/{review}/apply', headers=HEADERS, json={
        'target': target, 'revision': body['revision'], 'fingerprint': body['fingerprint'], 'changes': changes(**change)})


def without(config, *keys) -> dict:
    return {key: value for key, value in sections(config).items() if key not in keys}


def test_a_suggestion_is_applied_to_this_agent_after_review(store, saved_agent):
    client = saved_agent[0]
    before = get_agent_config('operations', conninfo=store.conninfo)
    review = saved_review(store, 'operations', changes(instructions='Gather evidence first.'))
    source = client.get(f'/api/agents/operations/reviews/{review}', headers=HEADERS)
    assert source.status_code == 200, source.text
    body = source.json()
    assert (body['suggestion'], body['changes']['instructions']) == ('Be clearer.', 'Gather evidence first.')
    assert (body['system_prompt'], body['instructions'], body['revision']) == (
        before['system_prompt']['content'], 'Verify every change.', before['revision'])
    host = before['ssh']['servers'][0]
    assert body['servers'] == [{'id': host['id'], 'name': host['name'], 'instructions': 'Verify every change.', 'inherits': True}]
    applied = apply_review(client, 'operations', review, body, system_prompt='Answer from evidence.',
                           instructions='Gather evidence first.')
    assert applied.status_code == 200, applied.text
    assert applied.json() == {'agent_id': 'operations'}
    after = get_agent_config('operations', conninfo=store.conninfo)
    assert (after['system_prompt'], after['instructions']) == ({'content': 'Answer from evidence.'}, 'Gather evidence first.')
    assert after['ssh']['servers'][0]['instructions'] == 'Gather evidence first.'  # an inheriting server follows
    assert without(after, 'prompt', 'instructions') == without(before, 'prompt', 'instructions')
    assert after['revision'] == before['revision'] + 1
    again = apply_review(client, 'operations', review, body, instructions='Older text.')
    assert again.status_code == 409 and again.json()['error']['message'] == AGENT_CHANGED
    with store.connect() as conn:
        audit = conn.execute("SELECT details FROM ka.audit_events WHERE action='agent.suggestion.applied'").fetchall()
    assert [row['details'] for row in audit] == [{'agent_id': 'operations', 'review_id': review, 'target': 'agent'}]


def test_server_instructions_become_an_override_and_unknown_servers_are_refused(store, saved_agent):
    client, server = saved_agent[0], saved_agent[1]
    review = saved_review(store, 'operations', changes())
    body = client.get(f'/api/agents/operations/reviews/{review}', headers=HEADERS).json()
    forged = apply_review(client, 'operations', review, body, servers=[{'server_id': str(uuid4()), 'instructions': 'Forged'}])
    assert forged.status_code == 422 and forged.json()['error']['message'] == UNKNOWN_SERVER
    blank = apply_review(client, 'operations', review, body, system_prompt='  ')
    assert blank.status_code == 400 and blank.json()['error']['message'] == PROMPT_REQUIRED
    applied = apply_review(client, 'operations', review, body,
                           servers=[{'server_id': server['id'], 'instructions': 'Check only the queue.'}])
    assert applied.status_code == 200, applied.text
    host = get_agent_config('operations', conninfo=store.conninfo)['ssh']['servers'][0]
    assert (host['instructions'], host['instructions_source']) == ('Check only the queue.', 'server_override')
    assert (host['skills_source'], host['mcp_source']) == ('agent', 'agent')  # a new override inherits the rest
    assert client.get(f'/api/agents/code/reviews/{review}', headers=HEADERS).status_code == 404  # another agent's


def test_a_suggestion_applied_to_a_new_clone_leaves_the_agent_as_it_was(store, saved_agent):
    client = saved_agent[0]
    before = get_agent_config('operations', conninfo=store.conninfo)
    review = saved_review(store, 'operations', changes(system_prompt='Answer from evidence.'))
    body = client.get(f'/api/agents/operations/reviews/{review}', headers=HEADERS).json()
    applied = apply_review(client, 'operations', review, body, target='clone', system_prompt='Answer from evidence.')
    assert applied.status_code == 200, applied.text
    clone_id = applied.json()['agent_id']
    assert clone_id != 'operations'
    assert sections(get_agent_config('operations', conninfo=store.conninfo)) == sections(before)
    copied = get_agent_config(clone_id, conninfo=store.conninfo)
    assert copied['system_prompt'] == {'content': 'Answer from evidence.'}
    assert without(copied, 'prompt') == without(before, 'prompt')
    assert client.get(f'/api/agents/{clone_id}', headers=HEADERS).json()['cloned_from'] == 'operations'


def test_a_suggestion_from_a_per_run_chat_never_narrows_the_agent(store, saved_agent):
    client = saved_agent[0]
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET resources='per_run' WHERE id='operations'")
    before = get_agent_config('operations', conninfo=store.conninfo)
    review = saved_review(store, 'operations', changes(instructions='Be brief.'), agent_config=NARROWED)
    body = client.get(f'/api/agents/operations/reviews/{review}', headers=HEADERS).json()
    assert apply_review(client, 'operations', review, body, instructions='Be brief.').status_code == 200
    after = get_agent_config('operations', conninfo=store.conninfo)
    assert without(after, 'instructions') == without(before, 'instructions')
    assert after['code']['projects'] == before['code']['projects']
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_agent_reviews.py -q`
Expected: FAIL: `ImportError: cannot import name 'AGENT_CHANGED'`.

- [ ] **Step 3: Implement**

Add to `agent_reviews.py` (imports: `from typing import Any, Literal`, `from fastapi import APIRouter, HTTPException,
Request, Response`, `from psycopg.types.json import Jsonb`, `from knowledge_admin.access import request_actor`, `from
knowledge_admin.agent_clones import clone`, `from knowledge_admin.knowledge_agents.config import fingerprint,
get_agent_config`):

```python
REVIEW_NOT_FOUND = 'Suggestion not found for this agent.'
AGENT_CHANGED = 'The agent changed since you opened the suggestion. Review it again.'
UNKNOWN_SERVER = 'Instructions refer to a server outside this agent.'
PROMPT_REQUIRED = 'Enter a system prompt.'
NO_CHANGES = {'system_prompt': None, 'instructions': None, 'server_instructions': []}


class ApplyInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    target: Literal['agent', 'clone']
    revision: int = Field(ge=1, strict=True)
    fingerprint: str = Field(min_length=64, max_length=64)
    changes: InstructionChanges


def write_changes(conn, project_id, agent_id: str, changes: InstructionChanges, actor_id) -> None:
    """Write reviewed texts into an agent: its prompt, its instructions (servers that inherit them follow) and server
    overrides (a new override inherits the agent's skills and MCP servers). Nothing else changes."""
    if changes.system_prompt is not None:
        conn.execute('UPDATE ka.agents SET system_prompt=%s WHERE id=%s', (changes.system_prompt, agent_id))
    if changes.instructions is not None:
        conn.execute("""INSERT INTO ka.agent_configs(project_id,agent,instructions,inherit_skills)
            VALUES (%s,%s,'',false) ON CONFLICT DO NOTHING""", (project_id, agent_id))
        conn.execute('''UPDATE ka.agent_configs SET instructions=%s,revision=revision+1,updated_by=%s
            WHERE project_id=%s AND agent=%s AND server_id IS NULL''', (changes.instructions, actor_id, project_id, agent_id))
    for change in changes.server_instructions:
        conn.execute("""INSERT INTO ka.agent_configs(project_id,agent,server_id,instructions,inherit_skills,inherit_mcp_servers)
            VALUES (%s,%s,%s,'',true,true) ON CONFLICT DO NOTHING""", (project_id, agent_id, change.server_id))
        conn.execute('''UPDATE ka.agent_configs SET instructions=%s,revision=revision+1,updated_by=%s
            WHERE project_id=%s AND agent=%s AND server_id=%s''',
            (change.instructions, actor_id, project_id, agent_id, change.server_id))
    conn.execute('UPDATE ka.agents SET revision=revision+1,updated_by=%s WHERE id=%s', (actor_id, agent_id))
    conn.execute('UPDATE ka.operations_settings SET revision=revision+1,updated_by=%s WHERE project_id=%s AND agent=%s',
                 (actor_id, project_id, agent_id))


def build_api(app_name, store):
    router = APIRouter(prefix='/agents/{agent_id}/reviews/{review_id}')

    def database():
        if store is None:
            raise HTTPException(503, 'Agent storage is not configured.')
        return store

    def review_of(conn, agent_id: str, review_id: UUID) -> dict[str, Any]:
        row = conn.execute('''SELECT r.suggestion,r.context FROM ka.agent_session_reviews r
            JOIN ka.agent_sessions s ON s.id=r.session_id WHERE r.id=%s AND s.agent=%s''', (review_id, agent_id)).fetchone()
        if row is None:
            raise HTTPException(404, REVIEW_NOT_FOUND)
        return row

    def config(agent_id: str) -> dict[str, Any]:
        try:
            return get_agent_config(agent_id, conninfo=store.conninfo)
        except LookupError:
            raise HTTPException(404, 'Agent not found') from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @router.get('')
    def source(agent_id: str, review_id: UUID, response: Response):
        """The suggestion with the agent's current texts, for the Apply suggestions sheet."""
        response.headers['Cache-Control'] = 'no-store'
        with database().connect() as conn:
            review = review_of(conn, agent_id, review_id)
        current = config(agent_id)
        return {'suggestion': review['suggestion'], 'changes': review['context'].get('changes', NO_CHANGES),
                'revision': current['revision'], 'fingerprint': fingerprint(current),
                'system_prompt': current['system_prompt']['content'], 'instructions': current['instructions'],
                'servers': [{'id': server['id'], 'name': server['name'], 'instructions': server['instructions'],
                             'inherits': server['instructions_source'] == 'agent'} for server in current['ssh']['servers']]}

    @router.post('/apply')
    def apply(agent_id: str, review_id: UUID, value: ApplyInput, request: Request):
        """Write the reviewed texts into this agent, or into a new clone of it (the agent then stays as it was)."""
        changes = value.changes
        if changes.system_prompt is not None and (not changes.system_prompt.strip() or '\x00' in changes.system_prompt):
            raise HTTPException(400, PROMPT_REQUIRED)
        with database().connect() as conn:
            conn.execute('SELECT id FROM ka.agents WHERE id=%s FOR UPDATE', (agent_id,))
            review_of(conn, agent_id, review_id)
            current = config(agent_id)
            if current['revision'] != value.revision or fingerprint(current) != value.fingerprint:
                raise HTTPException(409, AGENT_CHANGED)
            servers = {str(server['id']) for server in current['ssh']['servers']}
            if any(str(change.server_id) not in servers for change in changes.server_instructions):
                raise HTTPException(422, UNKNOWN_SERVER)
            actor, project = store.actor(conn, *request_actor(request)), store.project(conn, app_name)
            target = agent_id
            if value.target == 'clone':
                target = clone(conn, agent_id, actor)['id']
                conn.execute('''INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,details)
                    VALUES (%s,%s,'agent.cloned','agent',%s)''', (project, actor, Jsonb({'agent_id': target, 'cloned_from': agent_id})))
            write_changes(conn, project, target, changes, actor)
            conn.execute('''INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,details)
                VALUES (%s,%s,'agent.suggestion.applied','agent',%s)''',
                (project, actor, Jsonb({'agent_id': target, 'review_id': str(review_id), 'target': value.target})))
            return {'agent_id': target}

    return router
```

`InstructionChanges.system_prompt` keeps `max_length=100000` (= the column limit). `api.py`: import `agent_reviews`
in the late import line and add `router.include_router(agent_reviews.build_api(app_name, repository_store))` after
`agent_clones`.

- [ ] **Step 4: Run the tests**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_agent_reviews.py tests/test_agent_clones.py -q`
Expected: PASS.

Run: `cd services/admin-api && uv run --frozen pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api
git commit -m "feat(admin-api): apply a reviewed suggestion to the agent or to a new clone

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: General shows the prompt; no Prompts or Versions tab

**Files:**
- Create: `services/admin-ui/app/components/AgentPromptCard.tsx`,
  `services/admin-ui/app/components/__tests__/agentPrompt.test.tsx`
- Modify: `services/admin-ui/app/components/AgentDetailView.tsx`, `AgentsView.tsx`, `AgentModelSelection.tsx`,
  `AgentWizard.tsx`, `services/admin-ui/app/lib/profiles.ts`, `lib/llm.ts`, `lib/claudeCode.ts`, `lib/agentWizard.ts`,
  `services/admin-ui/app/routes/prompts.tsx`
- Delete: `components/PromptsView.tsx`, `components/AgentInstructions.tsx`, `components/AgentVersionsView.tsx`,
  `lib/prompts.ts`, `lib/agentVersions.ts`, `components/__tests__/prompts.test.tsx`,
  `components/__tests__/agentVersions.test.tsx`
- Modify tests: `agentTabs.test.tsx`, `agentTypes.test.tsx`, `agentTools.test.tsx`, `operations.test.tsx`

**Interfaces:**
- Consumes: `PUT /api/agents/{id}/prompt` (Task 1).
- Produces: `AgentProfile.system_prompt: string`, `cloned_from?: string | null`, `original?: { id: string; name:
  string } | null` (no `active_prompt`, `active_config_version_id`); `AgentSummary.original_name?: string | null` (no
  `config_version`, `prompt_title`, `prompt_version`); `saveAgentPrompt(id, revision, text)`;
  `lib/llm.ts` exports `AgentName`, `agentNames`; `AgentPromptCard({ profile, onSaved, onDirtyChange })`.

- [ ] **Step 1: Write the failing tests**

Create `services/admin-ui/app/components/__tests__/agentPrompt.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useParams } from 'react-router';
import AgentDetailView from '../AgentDetailView';
import PromptsPage from '../../routes/prompts';
import * as profiles from '../../lib/profiles';
import * as llm from '../../lib/llm';

vi.mock('../../lib/profiles', async () => ({ ...await vi.importActual('../../lib/profiles'), getAgent: vi.fn(),
  getAgentOptions: vi.fn(), saveAgentPrompt: vi.fn() }));
vi.mock('../../lib/llm', async () => ({ ...await vi.importActual('../../lib/llm'), getLlmSettings: vi.fn() }));
vi.mock('../OperationsView', () => ({ default: () => null }));

const agent: profiles.AgentProfile = { id: 'custom', name: 'Payments analyst', description: '', when_to_use: '',
  enabled: true, runtime: 'openai', documents_enabled: false, repositories_enabled: false, ssh_enabled: false,
  skills_enabled: false, mcp_enabled: false, services_enabled: false, documents_grounded: false, revision: 4,
  model_id: null, all_collections: false, all_repositories: false, collection_ids: [], repository_ids: [],
  service_ids: [], skill_ids: [], system_prompt: 'Use evidence.' };

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ is_admin: true })));
  vi.mocked(profiles.getAgent).mockImplementation(async () => structuredClone(agent));
  vi.mocked(profiles.getAgentOptions).mockResolvedValue({ collections: [], repositories: [], services: [], skills: [] });
  vi.mocked(llm.getLlmSettings).mockResolvedValue({ models: [], assignments: { search_model_id: null, code_model_id: null, operations_model_id: null, revision: 1 } });
});
afterEach(() => vi.unstubAllGlobals());
function Detail() { const { id } = useParams(); return <AgentDetailView key={id} agentId={id!} />; }
const open = (path: string) => render(<MemoryRouter initialEntries={[path]}><Routes>
  <Route path="/agents/:id" element={<Detail />} /><Route path="/llm-settings/prompts" element={<PromptsPage />} />
</Routes></MemoryRouter>);

test('the agent page has no Prompts or Versions tab', async () => {
  open('/agents/custom');
  await screen.findByRole('heading', { name: 'Payments analyst', level: 2 });
  expect(screen.getAllByRole('tab').map(tab => tab.textContent)).toEqual(
    ['General', 'Tools', 'Skills', 'Documents', 'Code projects', 'SSH', 'MCP', 'Services', 'Test']);
});

test('General edits and saves the prompt with the agent revision', async () => {
  const user = userEvent.setup();
  vi.mocked(profiles.saveAgentPrompt).mockImplementation(async (id, revision, text) => ({ ...structuredClone(agent), id, revision: revision + 1, system_prompt: text }));
  open('/agents/custom');
  const prompt = await screen.findByRole('textbox', { name: 'System prompt' });
  expect(prompt).toHaveValue('Use evidence.');
  expect(screen.getByRole('button', { name: 'Save prompt' })).toBeDisabled();
  await user.type(prompt, ' Cite sources.');
  await user.click(screen.getByRole('button', { name: 'Save prompt' }));
  expect(await screen.findByText('System prompt saved.')).toBeVisible();
  expect(profiles.saveAgentPrompt).toHaveBeenCalledWith('custom', 4, 'Use evidence. Cite sources.');
  expect(screen.getByRole('button', { name: 'Save prompt' })).toBeDisabled();
});

test('a refused save keeps the edit and says why', async () => {
  const user = userEvent.setup();
  vi.mocked(profiles.saveAgentPrompt).mockRejectedValue(new Error('Agent changed. Reload before saving.'));
  open('/agents/custom');
  const prompt = await screen.findByRole('textbox', { name: 'System prompt' });
  await user.clear(prompt);
  await user.type(prompt, 'Shorter.');
  await user.click(screen.getByRole('button', { name: 'Save prompt' }));
  expect(await screen.findByText('Agent changed. Reload before saving.')).toBeVisible();
  expect(prompt).toHaveValue('Shorter.');
});

test('old prompt links open the agent General tab', async () => {
  open('/llm-settings/prompts?agent=code');
  expect(await screen.findByRole('tab', { name: 'General' })).toHaveAttribute('aria-selected', 'true');
  expect(await screen.findByRole('textbox', { name: 'System prompt' })).toHaveValue('Use evidence.');
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd services/admin-ui && pnpm exec vitest run app/components/__tests__/agentPrompt.test.tsx`
Expected: FAIL: the tab list still has `Prompts` and `Versions`; no `System prompt` textbox.

- [ ] **Step 3: Implement**

`lib/profiles.ts`: in `AgentProfile` replace `active_config_version_id?: string | null;` and `active_prompt: {…} |
null;` with `system_prompt: string; cloned_from?: string | null; original?: { id: string; name: string } | null;`;
`AgentSummary` becomes `export interface AgentSummary extends AgentProfile { accepts?: ResourceLimit[]; model_name:
string | null; original_name?: string | null; tools_summary?: ToolsSummary }`; add

```ts
export const saveAgentPrompt = (id: string, revision: number, system_prompt: string) =>
  request<AgentProfile>(path(id) + '/prompt', json('PUT', { revision, system_prompt }));
```

`lib/llm.ts`: replace `import type { AgentName } from './prompts';` with

```ts
export type AgentName = 'search' | 'code' | 'operations';
export const agentNames: Record<AgentName, string> = { search: 'Search agent', code: 'Code search agent', operations: 'SSH agent' };
```

`AgentModelSelection.tsx`: `import { agentNames, type AgentName } from '../lib/llm';` (merge with its existing
`lib/llm` import).

Create `services/admin-ui/app/components/AgentPromptCard.tsx`:

```tsx
import { useEffect, useState } from 'react';
import { saveAgentPrompt, type AgentProfile } from '../lib/profiles';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Button } from '~/components/ui/button';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '~/components/ui/card';
import { Field, FieldDescription, FieldLabel } from '~/components/ui/field';
import { Spinner } from '~/components/ui/spinner';
import { Textarea } from '~/components/ui/textarea';

export const PROMPT_LIMIT = 100000;

/** The agent's system prompt: one text, saved with the agent's revision. What is saved here is what runs. */
export default function AgentPromptCard({ profile, onSaved, onDirtyChange }: { profile: AgentProfile; onSaved: (profile: AgentProfile) => void; onDirtyChange?: (dirty: boolean) => void }) {
  const [text, setText] = useState(profile.system_prompt);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  useEffect(() => { setText(profile.system_prompt); }, [profile.id, profile.system_prompt]);
  const dirty = text !== profile.system_prompt;
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);
  async function save() {
    setBusy(true); setError(''); setNotice('');
    try { onSaved(await saveAgentPrompt(profile.id, profile.revision, text)); setNotice('System prompt saved.'); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not save the system prompt.'); }
    finally { setBusy(false); }
  }
  return <Card><form onSubmit={event => { event.preventDefault(); if (dirty && text.trim() && !busy) void save(); }}>
    <CardHeader><CardTitle>System prompt</CardTitle><CardDescription>Required. What this agent runs with: changes apply to new conversations and runs; running conversations keep the configuration they started with.</CardDescription></CardHeader>
    <CardContent className="flex flex-col gap-3">
      {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
      <Field><FieldLabel htmlFor="agent-system-prompt">System prompt</FieldLabel>
        <Textarea id="agent-system-prompt" rows={12} maxLength={PROMPT_LIMIT} value={text} disabled={busy}
          onChange={event => { setText(event.target.value); setNotice(''); }} />
        <FieldDescription>{text.length.toLocaleString()} / {PROMPT_LIMIT.toLocaleString()} characters</FieldDescription></Field>
      {notice && <p role="status" className="text-sm text-muted-foreground">{notice}</p>}
    </CardContent>
    <CardFooter className="flex-wrap gap-2">
      <Button disabled={busy || !dirty || !text.trim()}>{busy && <Spinner data-icon="inline-start" />}Save prompt</Button>
      <Button type="button" variant="outline" disabled={busy || !dirty} onClick={() => { setText(profile.system_prompt); setError(''); }}>Discard prompt changes</Button>
    </CardFooter>
  </form></Card>;
}
```

`AgentDetailView.tsx`:
- imports: drop `AgentInstructions`, `PromptsView`, `AgentVersionsView`; add `import AgentPromptCard from './AgentPromptCard';`.
- `const sections = ['general','tools','skills','documents','git','ssh','mcp','services','test'];`
- state: delete `promptRevision`; add `const [promptDirty, setPromptDirty] = useState(false);`.
- delete the `{profile.active_config_version_id && …}` alert and the `{view === 'versions' && <AgentVersionsView …/>}` line.
- `AgentProfileSettings` wrapper: `hidden={['test','tools'].includes(view)}`.
- after it add `<div hidden={view !== 'general'}><AgentPromptCard profile={profile} onSaved={setProfile} onDirtyChange={setPromptDirty} /></div>`.
- `OperationsView`: `unsavedProfile={profileDirty || promptDirty}`.
- the Claude Code Test card text: `…with their saved configuration: the prompt, instructions, skills, Claude Code tools and code projects. …`.
- delete the `PromptsView` and `AgentInstructions` lines at the end.

`routes/prompts.tsx`: `return <Navigate to={`/agents/${agent}?view=general`} replace />;`

`AgentsView.tsx`: delete the `{a.config_version && <Badge …>Agent v…</Badge>}` element, the `System prompt`
`TableHead` and its `TableCell`; the removal dialog text becomes `Remove {removing?.name} and its saved
configuration? Shared servers, code projects, skills, models, and documents will be kept.`

`AgentWizard.tsx`: the prompt `FieldDescription` becomes `Required. Saved as this agent’s system prompt; edit it later
on its General tab.`

`lib/agentWizard.ts`: `existing_agents: { name: string; description: string; when_to_use: string }[]` (Task 1's
inventory no longer sends `prompt_title`).

`lib/claudeCode.ts`: `CATALOG_TOOLS_NEXT = 'Claude Code agents get these tools in the next update. What you save here is
already part of the agent’s configuration.'`.

Then `git rm` the seven files listed under **Delete**.

- [ ] **Step 4: Update the existing tests**

- `agentTabs.test.tsx`: delete `import * as prompts …`, the `vi.mock('../../lib/prompts', …)` line, `let data`, the
  `data = {…}` block and `vi.mocked(prompts.getPrompts)…`; use `llm.agentNames[id as llm.AgentName]`; in the agent
  fixture replace `active_prompt: null, model_name: null, prompt_title: …, prompt_version: 3` with `system_prompt:
  \`${id} instructions\`, model_name: null`. In the first test replace the `Active system prompt: operations prompt`
  link assertion with `expect(await screen.findByRole('textbox', { name: 'System prompt' })).toHaveValue('operations
  instructions');`. Delete `a custom agent prompt draft survives section changes and saves to its own identity` and
  `activating a prompt refreshes the Test preview`. `old prompt links open the matching agent detail` expects the
  `General` tab selected and the `System prompt` textbox with `code instructions` (instead of the Prompts tab and
  region). The tab list expectation drops `'Prompts','Versions'`.
- `agentTypes.test.tsx`: delete the `PromptsView`, `AgentInstructions` and `AgentVersionsView` mocks; `writer` gets
  `system_prompt: 'You are an expert technical writer.'` instead of `active_prompt`; `TABS` drops `'Prompts',
  'Versions'`; in `tool tabs say what Claude Code gets now, and Test starts a session` delete the two `Versions`
  lines; rename `a native agent keeps its test chat and Test vN` to `a native agent keeps its test chat`; drop
  `prompt_title` and `prompt_version` from every `getAgents` fixture row (no longer `AgentSummary` fields).
- `agentTools.test.tsx`: delete the `PromptsView`, `AgentInstructions` and `AgentVersionsView` mocks; `active_prompt:
  null` → `system_prompt: ''`.
- `operations.test.tsx`: `active_prompt: null` → `system_prompt: ''`.

- [ ] **Step 5: Run the tests**

Run: `cd services/admin-ui && pnpm exec vitest run app/components/__tests__/agentPrompt.test.tsx app/components/__tests__/agentTabs.test.tsx app/components/__tests__/agentTypes.test.tsx app/components/__tests__/agentTools.test.tsx app/components/__tests__/operations.test.tsx`
Expected: PASS.

Run: `cd services/admin-ui && pnpm exec tsc --noEmit && pnpm exec vitest run`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/admin-ui
git commit -m "feat(admin-ui): the prompt is edited on General; the Prompts and Versions tabs go

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: The Test tab without versions; suggestions apply to the agent or a new clone

**Files:**
- Create: `services/admin-ui/app/lib/reviews.ts`, `services/admin-ui/app/components/SuggestionApply.tsx`,
  `services/admin-ui/app/components/__tests__/suggestionApply.test.tsx`
- Modify: `services/admin-ui/app/components/AgentTestView.tsx`, `OperationsView.tsx`, `AgentDetailView.tsx`,
  `services/admin-ui/app/lib/operations.ts`, `lib/profiles.ts`
- Modify tests: `agentSessions.test.tsx`

**Interfaces:**
- Consumes: Task 6's API.
- Produces (`lib/reviews.ts`): `InstructionChanges`, `ReviewServer`, `ReviewSource`, `getReviewSource(agentId,
  reviewId)`, `applyReview(agentId, reviewId, source, target, changes)`; `SuggestionApply({ agentId, review, onClose,
  onApplied })`; `startOperationsRun(task, agentId, signal, onEvent, sessionId?, resources?)`;
  `getAgentConfig(id)`, `getRunResourceOptions(id)`; `OperationsView`/`AgentTestView` prop `onAgentChanged?: () => void`.

- [ ] **Step 1: Write the failing tests**

Create `services/admin-ui/app/components/__tests__/suggestionApply.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import SuggestionApply from '../SuggestionApply';
import * as api from '../../lib/reviews';
vi.mock('../../lib/reviews');
vi.mock('../Markdown', () => ({ default: ({ content }: { content: string }) => <div>{content}</div> }));

const review = { id: 'review-1', comment: 'Be clearer', suggestion: 'Be clearer.', created_at: '2026-10-08T10:00:00Z' };
const source: api.ReviewSource = { suggestion: 'Be clearer.', revision: 3, fingerprint: 'f'.repeat(64),
  changes: { system_prompt: null, instructions: 'State uncertainty.', server_instructions: [] },
  system_prompt: 'Use evidence.', instructions: 'Original instructions',
  servers: [{ id: 'server-1', name: 'Test server', instructions: 'Original instructions', inherits: true },
            { id: 'server-2', name: 'Queue host', instructions: 'Inspect only.', inherits: false }] };
const applied = vi.fn();
function page() { return render(<SuggestionApply agentId="custom" review={review} onClose={vi.fn()} onApplied={applied} />); }
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.getReviewSource).mockResolvedValue(structuredClone(source));
  vi.mocked(api.applyReview).mockResolvedValue({ agent_id: 'custom' });
});

it('prefills the proposed texts, lets inheriting servers follow and applies to this agent', async () => {
  page();
  expect(await screen.findByLabelText('Additional instructions')).toHaveValue('State uncertainty.');
  expect(api.getReviewSource).toHaveBeenCalledWith('custom', 'review-1');
  expect(screen.getByLabelText('System prompt')).toHaveValue('Use evidence.');
  expect(screen.getByLabelText('Test server instructions')).toHaveValue('State uncertainty.');
  expect(screen.getByLabelText('Queue host instructions')).toHaveValue('Inspect only.');
  fireEvent.change(screen.getByLabelText('Additional instructions'), { target: { value: 'Separate facts from guesses.' } });
  expect(screen.getByLabelText('Test server instructions')).toHaveValue('Separate facts from guesses.');
  fireEvent.change(screen.getByLabelText('Queue host instructions'), { target: { value: 'Check the queue only.' } });
  fireEvent.click(screen.getByRole('button', { name: 'Apply to this agent' }));
  await waitFor(() => expect(api.applyReview).toHaveBeenCalledWith('custom', 'review-1', source, 'agent', {
    system_prompt: null, instructions: 'Separate facts from guesses.',
    server_instructions: [{ server_id: 'server-2', instructions: 'Check the queue only.' }] }));
  expect(applied).toHaveBeenCalledWith('agent', 'custom');
});

it('applies to a new clone and keeps the edits when applying fails', async () => {
  vi.mocked(api.applyReview)
    .mockRejectedValueOnce(new Error('The agent changed since you opened the suggestion. Review it again.'))
    .mockResolvedValueOnce({ agent_id: 'clone-1' });
  page();
  fireEvent.change(await screen.findByLabelText('System prompt'), { target: { value: 'Answer from evidence.' } });
  fireEvent.click(screen.getByRole('button', { name: 'Apply to a new clone' }));
  expect(await screen.findByText('The agent changed since you opened the suggestion. Review it again.')).toBeVisible();
  expect(screen.getByLabelText('System prompt')).toHaveValue('Answer from evidence.');
  fireEvent.click(screen.getByRole('button', { name: 'Apply to a new clone' }));
  await waitFor(() => expect(applied).toHaveBeenCalledWith('clone', 'clone-1'));
  const [, , , target, changes] = vi.mocked(api.applyReview).mock.calls[1];
  expect([target, changes.system_prompt]).toEqual(['clone', 'Answer from evidence.']);
});

it('needs a system prompt', async () => {
  page();
  fireEvent.change(await screen.findByLabelText('System prompt'), { target: { value: '  ' } });
  expect(screen.getByRole('button', { name: 'Apply to this agent' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Apply to a new clone' })).toBeDisabled();
});
```

In `agentSessions.test.tsx`:
- replace `expect(screen.getByRole('link',{name:'Edit system prompt'})).toHaveAttribute(…)` with
  `expect(screen.getByRole('button',{name:'Review and apply'})).toBeVisible();`
- `previews the pinned session configuration instead of newly edited settings`: replace
  `expect(await screen.findByText('Agent v1 · Baseline')).toBeVisible();` with
  `expect(await screen.findByText('Saved session configuration')).toBeVisible();`
- replace `sends the selected version for a new test` with:

```tsx
it('a new test sends no version',async()=>{
  renderPage('?view=test&version_id=v2');
  expect(await screen.findByText('Current agent configuration')).toBeVisible();
  expect(screen.queryByRole('link',{name:'Choose version'})).toBeNull();
  fireEvent.change(screen.getByLabelText('Task'),{target:{value:'Try it'}});
  await waitFor(()=>expect(screen.getByRole('button',{name:'Run task'})).toBeEnabled());
  expect(getRunResourceOptions).toHaveBeenCalledWith('custom');
  fireEvent.click(screen.getByRole('button',{name:'Run task'}));
  await waitFor(()=>expect(api.startOperationsRun).toHaveBeenCalledWith('Try it','custom',expect.any(AbortSignal),expect.any(Function),undefined,undefined));
});
```

- every `mock.calls[n][6]` (resources) becomes `mock.calls[n][5]`.

- [ ] **Step 2: Run them to verify they fail**

Run: `cd services/admin-ui && pnpm exec vitest run app/components/__tests__/suggestionApply.test.tsx app/components/__tests__/agentSessions.test.tsx`
Expected: FAIL: `Failed to resolve import "../SuggestionApply"`; `Review and apply` not found.

- [ ] **Step 3: Implement**

Create `services/admin-ui/app/lib/reviews.ts`:

```ts
import { request } from './api';

export interface InstructionChanges { system_prompt: string | null; instructions: string | null; server_instructions: { server_id: string; instructions: string }[] }
/** A selected SSH server: `inherits` while its instructions are the agent's additional instructions. */
export interface ReviewServer { id: string; name: string; instructions: string; inherits: boolean }
export interface ReviewSource {
  suggestion: string; changes: InstructionChanges; revision: number; fingerprint: string;
  system_prompt: string; instructions: string; servers: ReviewServer[];
}
const path = (agentId: string, reviewId: string) => `/api/agents/${encodeURIComponent(agentId)}/reviews/${encodeURIComponent(reviewId)}`;
export const getReviewSource = (agentId: string, reviewId: string) => request<ReviewSource>(path(agentId, reviewId));
export const applyReview = (agentId: string, reviewId: string, source: ReviewSource, target: 'agent' | 'clone', changes: InstructionChanges) =>
  request<{ agent_id: string }>(path(agentId, reviewId) + '/apply', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ target, revision: source.revision, fingerprint: source.fingerprint, changes }) });
```

Create `services/admin-ui/app/components/SuggestionApply.tsx`:

```tsx
import { useEffect, useState } from 'react';
import { applyReview, getReviewSource, type InstructionChanges, type ReviewSource } from '../lib/reviews';
import type { SessionReview } from '../lib/operations';
import Markdown from './Markdown';
import { Alert, AlertDescription } from './ui/alert';
import { Button } from './ui/button';
import { Field, FieldDescription, FieldGroup, FieldLabel } from './ui/field';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from './ui/sheet';
import { Skeleton } from './ui/skeleton';
import { Textarea } from './ui/textarea';

/** Review a tuning suggestion's proposed texts, edit them, then write them into this agent or into a new clone. */
export default function SuggestionApply({ agentId, review, onClose, onApplied }: { agentId: string; review: SessionReview | null; onClose: () => void; onApplied: (target: 'agent' | 'clone', agentId: string) => void }) {
  const [source, setSource] = useState<ReviewSource>();
  const [prompt, setPrompt] = useState('');
  const [instructions, setInstructions] = useState('');
  const [servers, setServers] = useState<Record<string, string>>({});
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!review) return;
    let live = true; setSource(undefined); setError('');
    getReviewSource(agentId, review.id).then(value => {
      if (!live) return;
      const proposed = value.changes.instructions ?? value.instructions;
      setSource(value); setPrompt(value.changes.system_prompt ?? value.system_prompt); setInstructions(proposed);
      setServers(Object.fromEntries(value.servers.map(server => [server.id,
        value.changes.server_instructions.find(change => change.server_id === server.id)?.instructions
          ?? (server.inherits ? proposed : server.instructions)])));
    }).catch(reason => { if (live) setError(reason instanceof Error ? reason.message : 'Could not load the suggestion.'); });
    return () => { live = false; };
  }, [agentId, review]);
  function editInstructions(next: string) {
    // Servers that inherit the instructions follow them until edited on their own.
    setServers(current => Object.fromEntries(Object.entries(current).map(([id, text]) =>
      [id, source?.servers.find(server => server.id === id)?.inherits && text === instructions ? next : text])));
    setInstructions(next);
  }
  function changes(value: ReviewSource): InstructionChanges {
    return {
      system_prompt: prompt === value.system_prompt ? null : prompt,
      instructions: instructions === value.instructions ? null : instructions,
      server_instructions: value.servers.filter(server => servers[server.id] !== (server.inherits ? instructions : server.instructions))
        .map(server => ({ server_id: server.id, instructions: servers[server.id] })),
    };
  }
  async function apply(target: 'agent' | 'clone') {
    if (!source || !review) return;
    setBusy(true); setError('');
    try { const result = await applyReview(agentId, review.id, source, target, changes(source)); onApplied(target, result.agent_id); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not apply the suggestion.'); }
    finally { setBusy(false); }
  }
  return <Sheet open={review !== null} onOpenChange={open => { if (!open && !busy) onClose(); }}>
    <SheetContent className="w-full overflow-y-auto sm:max-w-3xl">
      <SheetHeader><SheetTitle>Apply suggestions</SheetTitle><SheetDescription>Review the proposed texts. Apply them to this agent, or to a new clone to test them first. Only the prompt and instructions change.</SheetDescription></SheetHeader>
      <div className="mt-6 flex flex-col gap-5">
        {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
        {!source ? !error && <Skeleton className="h-48 w-full" /> : <FieldGroup>
          <details><summary className="cursor-pointer text-sm font-medium">Reviewer’s suggestions</summary><div className="mt-3"><Markdown content={source.suggestion} /></div></details>
          <Field><FieldLabel htmlFor="apply-system-prompt">System prompt</FieldLabel><Textarea id="apply-system-prompt" rows={10} maxLength={100000} value={prompt} disabled={busy} onChange={event => setPrompt(event.target.value)} /></Field>
          <Field><FieldLabel htmlFor="apply-instructions">Additional instructions</FieldLabel><Textarea id="apply-instructions" rows={7} maxLength={40000} value={instructions} disabled={busy} onChange={event => editInstructions(event.target.value)} /></Field>
          {source.servers.map(server => <Field key={server.id}><FieldLabel htmlFor={`apply-server-${server.id}`}>{server.name} instructions</FieldLabel>
            <Textarea id={`apply-server-${server.id}`} rows={4} maxLength={40000} value={servers[server.id] ?? ''} disabled={busy} onChange={event => setServers(current => ({ ...current, [server.id]: event.target.value }))} />
            {server.inherits && <FieldDescription>Follows the additional instructions unless you change it here.</FieldDescription>}</Field>)}
          <div className="flex flex-wrap gap-2">
            <Button type="button" disabled={busy || !prompt.trim()} onClick={() => void apply('agent')}>Apply to this agent</Button>
            <Button type="button" variant="outline" disabled={busy || !prompt.trim()} onClick={() => void apply('clone')}>Apply to a new clone</Button>
            <Button type="button" variant="ghost" disabled={busy} onClick={onClose}>Cancel</Button>
          </div>
        </FieldGroup>}
      </div>
    </SheetContent>
  </Sheet>;
}
```

`lib/operations.ts`: `OperationsRun.context.prompt` becomes `prompt?: { version?: number; content?: string }`;
`AgentSession` drops `agent_version_id` and `config_version`;

```ts
export async function startOperationsRun(task: string, agentId: string, signal: AbortSignal, onEvent: (event: RunEvent) => void, sessionId?: string, resources?: RunResourcesInput) {
  const response = await fetch(runsPath(agentId), { ...json('POST', { id: crypto.randomUUID(), task, session_id: sessionId, is_test: true, ...(resources ? { resources } : {}) }), signal });
```

(the rest of the function unchanged).

`lib/profiles.ts`: `export const getAgentConfig = (id: string) => request<Record<string, unknown>>(path(id) +
'/config');` and `export const getRunResourceOptions = (id: string) => request<RunResourceOptions>(path(id) +
'/run-resources');`.

`AgentTestView.tsx`:
- props gain `onAgentChanged?: () => void`; import `useNavigate` from `react-router`, `SuggestionApply`, and `type
  SessionReview` from `../lib/operations`; add `const navigate = useNavigate();` and `const [applying, setApplying] =
  useState<SessionReview | null>(null);`.
- delete `const versionId = params.get('version_id');`; `getRunResourceOptions(agentId)` with dependencies `[agentId,
  revision, runOptionsRetry]`; the config effect: `if (!showConfig || !active || dirty) return;`,
  `getAgentConfig(agentId)`, dependencies `[showConfig, agentId, active, dirty, revision, conversation]`.
- `selectSession`: `if (id) current.set('session_id',id); else current.delete('session_id');`.
- `startOperationsRun(…, sessionId ?? undefined, !sessionId && perRun ? runResources : undefined)`.
- delete `pinnedVersion`; the configuration line becomes
  `<span>{conversation ? 'Saved session configuration' : 'Current agent configuration'}</span><span>· {conversation ?
  'Pinned to this conversation' : 'Pinned when the conversation starts'}</span>` (the `Choose version` link goes).
- the activity summary becomes `View activity · {run.context.model_name}`.
- in each suggestion, replace the `Save as new version`, `Edit system prompt` and `Edit instructions` buttons with
  `<Button size="sm" onClick={() => setApplying(review)}>Review and apply</Button>` (keep `Copy suggestions`).
- before the final closing `</div>` render:

```tsx
    <SuggestionApply agentId={agentId} review={applying} onClose={() => setApplying(null)} onApplied={(target, id) => {
      setApplying(null); setPanel(null);
      if (target === 'clone') navigate(`/agents/${encodeURIComponent(id)}?view=test`);
      else { setNotice('Applied to this agent. New conversations use it.'); onAgentChanged?.(); }
    }} />
```

`OperationsView.tsx`: add `onAgentChanged?: () => void` to its props and pass `onAgentChanged={onAgentChanged}` to
`AgentTestView`. `AgentDetailView.tsx`: pass `onAgentChanged={() => setReload(n => n + 1)}` to `OperationsView`.

- [ ] **Step 4: Run the tests**

Run: `cd services/admin-ui && pnpm exec vitest run app/components/__tests__/suggestionApply.test.tsx app/components/__tests__/agentSessions.test.tsx app/lib/__tests__/execution.test.ts`
Expected: PASS.

Run: `cd services/admin-ui && pnpm exec tsc --noEmit && pnpm exec vitest run`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-ui
git commit -m "feat(admin-ui): Test tab without versions; suggestions apply to this agent or a new clone

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Clone and Apply on the agent page and in All agents

**Files:**
- Create: `services/admin-ui/app/lib/clones.ts`, `services/admin-ui/app/components/AgentActions.tsx`,
  `services/admin-ui/app/components/__tests__/agentClones.test.tsx`
- Modify: `services/admin-ui/app/components/AgentDetailView.tsx`, `AgentsView.tsx`

**Interfaces:**
- Consumes: Tasks 4–5's API, `deleteAgent`, `getAgentHistory`, `describeHistory`.
- Produces (`lib/clones.ts`): `ApplyPreview`, `cloneAgent(id)`, `getApplyPreview(id)`, `applyToOriginal(id,
  preview)`; `AgentActions({ profile, onChanged })`.

- [ ] **Step 1: Write the failing tests**

Create `services/admin-ui/app/components/__tests__/agentClones.test.tsx`:

```tsx
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useParams } from 'react-router';
import AgentDetailView from '../AgentDetailView';
import AgentsView from '../AgentsView';
import * as profiles from '../../lib/profiles';
import * as clones from '../../lib/clones';
import * as llm from '../../lib/llm';
import { Toaster } from '~/components/ui/sonner';

vi.mock('../../lib/profiles', async () => ({ ...await vi.importActual('../../lib/profiles'), getAgent: vi.fn(),
  getAgents: vi.fn(), getAgentOptions: vi.fn(), deleteAgent: vi.fn(), getAgentHistory: vi.fn() }));
vi.mock('../../lib/clones');
vi.mock('../../lib/llm', async () => ({ ...await vi.importActual('../../lib/llm'), getLlmSettings: vi.fn() }));
vi.mock('../OperationsView', () => ({ default: () => null }));
vi.mock('../AgentWizard', () => ({ default: () => null }));

const base: profiles.AgentProfile = { id: 'operations', name: 'SSH agent', description: '', when_to_use: '', enabled: true,
  runtime: 'openai', documents_enabled: false, repositories_enabled: false, ssh_enabled: true, skills_enabled: false,
  mcp_enabled: false, services_enabled: false, documents_grounded: false, revision: 3, model_id: null,
  all_collections: false, all_repositories: false, collection_ids: [], repository_ids: [], service_ids: [],
  skill_ids: [], system_prompt: 'Operate servers.', cloned_from: null, original: null };
const copy: profiles.AgentProfile = { ...base, id: 'clone-1', name: 'SSH agent (copy)', revision: 2,
  cloned_from: 'operations', original: { id: 'operations', name: 'SSH agent' } };
const preview: clones.ApplyPreview = { original: { id: 'operations', name: 'SSH agent', revision: 3 },
  fingerprint: 'f'.repeat(64), changes: ['Prompt', 'Tools'] };

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ is_admin: true })));
  vi.mocked(profiles.getAgent).mockImplementation(async id => structuredClone(id === 'clone-1' ? copy : { ...base, id }));
  vi.mocked(profiles.getAgents).mockResolvedValue({ agents: [
    { ...structuredClone(base), model_name: null, original_name: null },
    { ...structuredClone(copy), model_name: null, original_name: 'SSH agent' }] });
  vi.mocked(profiles.getAgentOptions).mockResolvedValue({ collections: [], repositories: [], services: [], skills: [] });
  vi.mocked(profiles.getAgentHistory).mockResolvedValue({ test_chats: 2, chats: 0, claude_code_sessions: 0, runs: 0 });
  vi.mocked(llm.getLlmSettings).mockResolvedValue({ models: [], assignments: { search_model_id: null, code_model_id: null, operations_model_id: null, revision: 1 } });
  vi.mocked(clones.cloneAgent).mockResolvedValue({ id: 'clone-2', name: 'SSH agent (copy 2)' });
  vi.mocked(clones.getApplyPreview).mockResolvedValue(structuredClone(preview));
  vi.mocked(clones.applyToOriginal).mockResolvedValue({ id: 'operations', revision: 4 });
});
afterEach(() => vi.unstubAllGlobals());
function Detail() { const { id } = useParams(); return <AgentDetailView key={id} agentId={id!} />; }
const open = (path: string) => render(<MemoryRouter initialEntries={[path]}><Routes>
  <Route path="/agents" element={<AgentsView />} /><Route path="/agents/:id" element={<Detail />} />
</Routes><Toaster /></MemoryRouter>);

test('Clone agent in the header menu opens the new clone', async () => {
  const user = userEvent.setup();
  open('/agents/operations');
  await user.click(await screen.findByRole('button', { name: 'Actions for SSH agent' }));
  await user.click(await screen.findByRole('menuitem', { name: 'Clone agent' }));
  expect(clones.cloneAgent).toHaveBeenCalledWith('operations');
  await waitFor(() => expect(profiles.getAgent).toHaveBeenLastCalledWith('clone-2'));
  expect(screen.queryByRole('button', { name: /^Apply to/ })).toBeNull();  // not a clone: nothing to apply to
});

test('a clone says what it is a clone of and applies to it after confirmation', async () => {
  const user = userEvent.setup();
  open('/agents/clone-1');
  expect(await screen.findByRole('link', { name: 'SSH agent' })).toHaveAttribute('href', '/agents/operations');
  expect(screen.getByText(/Clone of/)).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Apply to SSH agent' }));
  const dialog = await screen.findByRole('alertdialog');
  expect(await within(dialog).findByText('These sections of SSH agent are replaced by this clone’s: Prompt, Tools. Its name and description stay.')).toBeVisible();
  expect(clones.applyToOriginal).not.toHaveBeenCalled();
  await user.click(within(dialog).getByRole('button', { name: 'Apply' }));
  await waitFor(() => expect(clones.applyToOriginal).toHaveBeenCalledWith('clone-1', preview));
  expect(await screen.findByText('Applied to SSH agent.')).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Delete this clone' }));
  await user.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Delete clone' }));
  await waitFor(() => expect(profiles.deleteAgent).toHaveBeenCalledWith('clone-1', 2, true));
  await waitFor(() => expect(profiles.getAgent).toHaveBeenLastCalledWith('operations'));
});

test('a refused apply keeps the dialog open with the reason', async () => {
  const user = userEvent.setup();
  vi.mocked(clones.applyToOriginal).mockRejectedValue(new Error('The original changed; review again.'));
  open('/agents/clone-1');
  await user.click(await screen.findByRole('button', { name: 'Apply to SSH agent' }));
  const dialog = await screen.findByRole('alertdialog');
  await within(dialog).findByText(/These sections of SSH agent/);
  await user.click(within(dialog).getByRole('button', { name: 'Apply' }));
  expect(await within(dialog).findByText('The original changed; review again.')).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Delete this clone' })).toBeNull();
});

test('All agents shows clones and clones a row', async () => {
  const user = userEvent.setup();
  open('/agents');
  expect(await screen.findByText('Clone of SSH agent')).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Clone SSH agent' }));
  expect(clones.cloneAgent).toHaveBeenCalledWith('operations');
  await waitFor(() => expect(profiles.getAgent).toHaveBeenLastCalledWith('clone-2'));
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd services/admin-ui && pnpm exec vitest run app/components/__tests__/agentClones.test.tsx`
Expected: FAIL: `Failed to resolve import "../../lib/clones"`.

- [ ] **Step 3: Implement**

Create `services/admin-ui/app/lib/clones.ts`:

```ts
import { request } from './api';

/** What Apply would replace on the original (section labels) and the fingerprint the dialog sends back. */
export interface ApplyPreview { original: { id: string; name: string; revision: number }; fingerprint: string; changes: string[] }
const path = (id: string) => `/api/agents/${encodeURIComponent(id)}`;
const post = (value?: unknown) => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, ...(value === undefined ? {} : { body: JSON.stringify(value) }) });
export const cloneAgent = (id: string) => request<{ id: string; name: string }>(path(id) + '/clone', post());
export const getApplyPreview = (id: string) => request<ApplyPreview>(path(id) + '/apply');
export const applyToOriginal = (id: string, preview: ApplyPreview) =>
  request<{ id: string; revision: number }>(path(id) + '/apply', post({ revision: preview.original.revision, fingerprint: preview.fingerprint }));
```

Create `services/admin-ui/app/components/AgentActions.tsx`:

```tsx
import { useState } from 'react';
import { useNavigate } from 'react-router';
import { Copy, MoreHorizontal } from 'lucide-react';
import { toast } from 'sonner';
import { applyToOriginal, cloneAgent, getApplyPreview, type ApplyPreview } from '../lib/clones';
import { deleteAgent, describeHistory, getAgentHistory, type AgentProfile } from '../lib/profiles';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '~/components/ui/alert-dialog';
import { Button } from '~/components/ui/button';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '~/components/ui/dropdown-menu';

const message = (reason: unknown, fallback: string) => reason instanceof Error ? reason.message : fallback;

/** The agent page header: Clone agent; on a clone, Apply to <original> and, once applied, Delete this clone. */
export default function AgentActions({ profile, onChanged }: { profile: AgentProfile; onChanged: () => void }) {
  const navigate = useNavigate();
  const original = profile.original ?? null;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [applying, setApplying] = useState(false);
  const [preview, setPreview] = useState<ApplyPreview | null>(null);
  const [applied, setApplied] = useState(false);
  const [deleting, setDeleting] = useState(false);
  async function clone() {
    setBusy(true); setError('');
    try { const created = await cloneAgent(profile.id); toast.success(`Clone created: ${created.name}`); navigate(`/agents/${encodeURIComponent(created.id)}`); }
    catch (reason) { setError(message(reason, 'Could not clone the agent.')); }
    finally { setBusy(false); }
  }
  async function openApply() {
    setApplying(true); setPreview(null); setError('');
    try { setPreview(await getApplyPreview(profile.id)); } catch (reason) { setError(message(reason, 'Could not compare the configurations.')); }
  }
  async function apply() {
    if (!preview) return;
    setBusy(true); setError('');
    try { await applyToOriginal(profile.id, preview); setApplying(false); setApplied(true); onChanged(); }
    catch (reason) { setError(message(reason, 'Could not apply the configuration.')); }
    finally { setBusy(false); }
  }
  async function removeClone() {
    if (!original) return;
    setBusy(true); setError('');
    try {
      const history = describeHistory(await getAgentHistory(profile.id));
      await deleteAgent(profile.id, profile.revision, Boolean(history));
      toast.success(`Clone ${profile.name} deleted`); navigate(`/agents/${encodeURIComponent(original.id)}`);
    } catch (reason) { setDeleting(false); setError(message(reason, 'Could not delete the clone.')); }
    finally { setBusy(false); }
  }
  return <div className="flex flex-wrap items-center gap-2">
    {original && <Button variant="outline" size="sm" disabled={busy} onClick={() => void openApply()}>Apply to {original.name}</Button>}
    {applied && original && <Button variant="destructive" size="sm" disabled={busy} onClick={() => setDeleting(true)}>Delete this clone</Button>}
    <DropdownMenu modal={false}><DropdownMenuTrigger asChild><Button variant="ghost" size="icon" aria-label={`Actions for ${profile.name}`} disabled={busy}><MoreHorizontal /></Button></DropdownMenuTrigger>
      <DropdownMenuContent align="end"><DropdownMenuItem onSelect={() => void clone()}><Copy />Clone agent</DropdownMenuItem></DropdownMenuContent></DropdownMenu>
    {applied && original && <p role="status" className="text-sm text-muted-foreground">Applied to {original.name}.</p>}
    {error && !applying && <p role="alert" className="text-sm text-destructive">{error}</p>}
    <AlertDialog open={applying} onOpenChange={open => { if (!open && !busy) setApplying(false); }}><AlertDialogContent>
      <AlertDialogHeader><AlertDialogTitle>Apply to {original?.name}?</AlertDialogTitle>
        <AlertDialogDescription>{!preview ? 'Comparing the configurations…' : preview.changes.length
          ? `These sections of ${original?.name} are replaced by this clone’s: ${preview.changes.join(', ')}. Its name and description stay.`
          : 'The original already runs this configuration.'}</AlertDialogDescription></AlertDialogHeader>
      {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
      <AlertDialogFooter><AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
        <AlertDialogAction disabled={busy || !preview?.changes.length} onClick={event => { event.preventDefault(); void apply(); }}>Apply</AlertDialogAction></AlertDialogFooter>
    </AlertDialogContent></AlertDialog>
    <AlertDialog open={deleting} onOpenChange={open => { if (!open && !busy) setDeleting(false); }}><AlertDialogContent>
      <AlertDialogHeader><AlertDialogTitle>Delete this clone?</AlertDialogTitle>
        <AlertDialogDescription>{profile.name} and its test chats are deleted. {original?.name} keeps the applied configuration.</AlertDialogDescription></AlertDialogHeader>
      <AlertDialogFooter><AlertDialogCancel disabled={busy}>Keep clone</AlertDialogCancel>
        <AlertDialogAction disabled={busy} onClick={event => { event.preventDefault(); void removeClone(); }}>Delete clone</AlertDialogAction></AlertDialogFooter>
    </AlertDialogContent></AlertDialog>
  </div>;
}
```

`AgentDetailView.tsx`: import `AgentActions`; the header row becomes `<div className="flex flex-wrap items-center
justify-between gap-3"><div className="flex flex-wrap items-center gap-3"><h2 …>{profile.name}</h2>{…badges…}</div>
<AgentActions profile={profile} onChanged={() => setReload(n => n + 1)} /></div>`; at the start of the
`TabsContent` add

```tsx
          {view === 'general' && profile.original && <p className="text-sm text-muted-foreground">Clone of <Link className="underline underline-offset-4" to={`/agents/${encodeURIComponent(profile.original.id)}`}>{profile.original.name}</Link></p>}
```

`AgentsView.tsx`: import `Copy` from `lucide-react` and `cloneAgent` from `../lib/clones`; add

```tsx
  async function cloneRow(agent: AgentSummary) {
    setBusy(true); setError('');
    try { const created = await cloneAgent(agent.id); toast.success(`Clone created: ${created.name}`); navigate(`/agents/${encodeURIComponent(created.id)}`); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not clone the agent.'); }
    finally { setBusy(false); }
  }
```

under the agent's name link add `{a.original_name && <p className="mt-1 text-xs text-muted-foreground">Clone of
{a.original_name}</p>}`, and between the Open and Remove buttons add `<Button variant="ghost" size="icon"
disabled={busy} aria-label={`Clone ${a.name}`} onClick={() => void cloneRow(a)}><Copy /></Button>`.

- [ ] **Step 4: Run the tests**

Run: `cd services/admin-ui && pnpm exec vitest run app/components/__tests__/agentClones.test.tsx app/components/__tests__/agentTabs.test.tsx app/components/__tests__/agentTypes.test.tsx`
Expected: PASS.

Run: `cd services/admin-ui && pnpm exec tsc --noEmit && pnpm exec vitest run`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-ui
git commit -m "feat(admin-ui): Clone agent, Apply to the original and Delete this clone

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Documentation and the full verification

**Files:**
- Modify: `docs/agents.md`, `docs/agent-profiles.md`, `docs/prompts.md`, `docs/migrations.md`, `docs/user-guide.md`,
  `docs/AGENTS.md`, `README.md`, `deploy/README.md`

- [ ] **Step 1: Update the docs**

- `docs/agents.md`: replace **Configuration versions** with **Clones**: one configuration per agent (what the tabs
  show runs); Clone agent (`POST /api/agents/{id}/clone`, "(copy N)", credentials referenced, history not copied,
  `cloned_from`); Apply to original (`GET|POST /api/agents/{id}/apply`, the section list, revision + fingerprint,
  refused while working, audit `agent.config.applied`); suggestions (`GET|POST
  /api/agents/{id}/reviews/{review_id}[/apply]`, to this agent or a new clone). Remove `version_id` from the sessions
  and runs API bullets; conversations pin the agent's configuration when they start.
- `docs/agent-profiles.md`: tabs table without **Prompts** and **Versions** (General holds the prompt); the prompt
  section (one field, `PUT /api/agents/{id}/prompt`, 100 000 characters, wizard saves it); migration notes for
  `0079_agent_system_prompt` and `0080_clone_agents`; deletion no longer mentions prompt versions; drop
  `?version_id=` from `run-resources`.
- `docs/prompts.md`: retitle **Agent system prompts**: the prompt is edited on the agent's General tab; history is
  not kept (clone the agent to try a different prompt); the `/api/llm/prompts` table goes; Docs → Prompts
  (documentation prompts) is unchanged and documented in `docs/documentation.md`.
- `docs/migrations.md`: `0079_agent_system_prompt` (column filled from the active prompt version; FK dropped; down
  restores prompt rows, an edited prompt becomes a new active version, text truncated to 40 000) and
  `0080_clone_agents` (materialises active versions incl. tools, overrides and connections; drops versions, prompt
  tables, view, guard function, version columns and constraints; adds `cloned_from`; down restores the empty schema).
- `docs/user-guide.md` (agents section): remove the Prompts/Versions rows and the "Working configuration vs. active
  version" note; add **Experiment on a clone** (Clone agent → change and test → Apply to <original> → Delete this
  clone) and **Apply suggestions** (Review and apply → Apply to this agent / Apply to a new clone); Claude Code agents
  no longer mention prompt versions; per-run agents: "the agent's setting decides the mode".
- `docs/AGENTS.md`, `README.md`: replace "Prompts tab … draft and active versions" with the General tab prompt and
  clones.
- `deploy/README.md`: a section **Upgrading to clones instead of versions**: 0080 makes each agent's active version
  its configuration and deletes configuration versions and prompt history — back up first; rolling back below 0080
  restores only the empty schema (0079 down gives each agent its current prompt as version 1).

- [ ] **Step 2: Run every suite**

Run: `cd services/admin-api && uv run --frozen pytest -q`
Expected: PASS.

Run: `bash db/tests/run.sh`
Expected: every `PASS:` line, exit 0.

Run: `cd services/admin-ui && pnpm exec tsc --noEmit && pnpm exec vitest run`
Expected: PASS.

Run: `git grep -n -E "agentVersions|AgentVersionsView|PromptsView|AgentInstructions|lib/prompts|/api/llm/prompts|version_id=|Test v\{|Choose version|\['active_prompt'\]|prompt_title" -- services packages`
Expected: no output (document and documentation-prompt `version_id` stay in `documents.py`, `documentation_prompts.py`
and `lib/documents.ts`, which this grep's `version_id=` pattern does not match).

- [ ] **Step 3: Commit**

```bash
git add docs README.md deploy/README.md
git commit -m "docs: clones instead of agent configuration and prompt versions

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

## Follow-ups (not in this plan)

- A side-by-side diff of the sections in the Apply dialog (today it lists which sections change).
- Comparing two clones directly ("which one answers better") in the Test tab.
