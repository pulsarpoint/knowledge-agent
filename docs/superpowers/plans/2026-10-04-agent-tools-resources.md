# Agent Tools and Resources Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace agent capability switches with "instructions + skills + tools + resources": a tool catalog (built-in and MCP) with editable usage docs, per-agent tool picks, per-agent resources expanded through services, typed SSH functions with an audit trail.

**Architecture:** A new module `knowledge_admin/agent_tools.py` owns the catalog constants and `upgrade_snapshot()`, which converts any configuration (old capability blocks or new shape) into the new shape. `get_agent_config` produces the new shape from `ka.agent_tools` and the existing resource tables; every runtime entry point calls `upgrade_snapshot()` so the six kinds of persisted snapshots keep working. SSH functions live in `knowledge_admin/tools/ssh.py` and write `ka.ssh_actions` through an audit sink configured at startup.

**Tech Stack:** Python 3.12, FastAPI, psycopg 3, openai-agents 0.19.2, asyncssh, PostgreSQL 17 (golang-migrate), React Router 7 + Vitest.

**Spec:** `docs/superpowers/specs/2026-10-04-agent-tools-resources-design.md`

## Decisions beyond the spec (rulings)

1. **One converter for every persisted snapshot.** `upgrade_snapshot(config)` is idempotent and is called by `create_agent`, `load_credentials`, `get_tools`, `get_prompt`, `knowledge_violations`, `reader_api.narrow`, the reader transcript, the tuning reviewer and the versions UI source. Old shapes in `agent_versions.config`, `agent_sessions.config`, `operations_runs.context.config`, `agent_board_tasks.config`, `agent_session_reviews.context.agent_config` and `documentation_generations.agent_snapshot` therefore keep working without a data migration.
2. **Synthetic configs.** The builders in `documentation_generation.py`, `documentation_updates.py`, `agent_wizard.py` and `scripts/benchmarks/*` move to the new shape through `agent_tools.make_config(...)`; old-shape dicts would still work through the converter.
3. **Other services keep reading `knowledge_llm.profile()`.** `profile()` adds derived `documents_enabled` and `repositories_enabled` values computed from `ka.agent_tools`, so docs-wiki `search_api`, code-agent and admin `code_api` need no change.
4. **Migration in two steps inside `0061_agent_tools`.** Task 1 adds tables and converts data. Task 7 adds the drops (capability columns, `documents_grounded`, `inherit_*`, `agent_config_skills`, `agent_config_mcp_servers`, the coordinator CHECK `coordinator_delegates_resources`) to the same files. The migration is unreleased, so editing it is safe.
5. **SSH audit sink.** `knowledge_admin/tools/ssh_audit.py` has `configure(store)` (called by `create_app` and the documentation worker's `main`) and `record(row)`. Callers put a non-secret `config['audit'] = {project_id, actor_id, run_id, session_id}` on the per-turn config copy before `agent()`. `agent()` and `create_agent()` signatures stay unchanged.
6. **`ssh_run` is renamed `run_command`** (spec catalog). Per-server MCP and skill overrides are removed (the spec's migration refuses if any exist). MCP is agent-level only.
7. **Dead code removed:** `/api/mcp/code` (GET/PUT), `CodeMCPSettings.tsx`, `SearchAgentView.tsx`, `CodeSearchAgentView.tsx` and their tests; `ka.operations_commands` is left in place (read-only legacy).
8. **Services context:** a selected service always contributes context. For converted old snapshots, services appear only if the old `services.enabled` was true.

## Global Constraints

- No new third-party dependencies.
- `agents.agent()` and `agents.create_agent()` signatures unchanged.
- Admin `POST /api/agents/{id}/runs` SSE output unchanged.
- Tool ids (exact): `documents_search`, `documents_report_gaps`, `code_reading`, `shell_sandbox`, `ssh_logs`, `ssh_diagnostics`, `ssh_commands`, `service_lookup`, `mcp:<mcp_server_id>`.
- Function names (exact): `search_documents`, `read_document`, `report_not_covered`, `list_files`, `read_file`, `search_files`, `code_search`, `ast_grep`, `code_graph`, `shell_run`, `service_logs`, `container_logs`, `tail_log`, `service_status`, `container_list`, `disk_usage`, `memory`, `listening_ports`, `http_check`, `run_command`, `lookup_service`.
- Only `run_command` may use sudo, and only when the server allows it.
- Every SSH function call, including refusals, writes one `ka.ssh_actions` row.
- Reader rule: only `documents_search`, `documents_report_gaps`, `service_lookup`; the first two required.
- Conflict message pattern: 409 "…changed. Reload before saving."
- Test commands: admin-api `cd services/admin-api && uv run --frozen pytest <files> -q`; UI `cd services/admin-ui && pnpm exec vitest run <files>` / `pnpm test`; DB `make db-test`.
- Known pre-existing failures to ignore: admin-api `tests/test_environments.py::test_migration_reapply_and_populated_rollback_protection`; docs-wiki `tests/test_deploy.py` (3).

## Review Focus

1. **A server reachable only through a service is removed from that service's deployments** (or the service is unselected) while a session is open: a new turn must not reach it — the call-time check uses the live effective servers for new sessions and the pinned list for pinned ones, and `load_credentials` re-checks `ssh_allowed`. Test: Task 3 `test_load_credentials_rechecks_effective_servers`.
2. **An agent has only `ssh_logs` and the model calls a function name it was not given** (e.g. crafted `run_command` call or `service_status`): refused and audited. Test: Task 4 `test_logs_only_agent_cannot_call_other_ssh_functions`.
3. **Old pinned session with grounded documents and code enabled** continues after the upgrade with the same tools. Test: Task 2 `test_upgrade_snapshot_grounded_with_code`.
4. **An MCP server is deleted while agents have its tool picked:** picks disappear by cascade and a pinned session that still lists it fails with a clear message instead of a crash. Test: Task 3 `test_pinned_mcp_tool_for_deleted_server_is_reported`.
5. **`tail_log` path tricks** (`/var/log/../../etc/shadow`, symlink out of `/var/log`, relative path): refused before or by the remote `readlink -f` check. Test: Task 4 `test_tail_log_rejects_escaping_paths`.

---

### Task 1: Migration `0061_agent_tools` — additive part and data conversion

**Files:**
- Create: `db/migrations/0061_agent_tools.up.sql`, `db/migrations/0061_agent_tools.down.sql`
- Modify: `db/tests/run.sh` (rollback-version expectation 59→60 pattern: the `down 1` check expects latest−1; update the number to 60)
- Test: `services/admin-api/tests/test_agent_tools_migration.py`

**Interfaces:**
- Produces: tables `ka.tools(id, source, mcp_server_id, name, description, usage, revision, updated_by, updated_at)`, `ka.agent_tools(agent_id, tool_id)`, `ka.ssh_actions(...)`; trigger keeping one `mcp:<id>` tool per MCP server; converted picks for existing agents.

- [ ] **Step 1: Write the failing test**

```python
"""Migration 0061 seeds the tool catalog and converts capability switches into tool picks."""
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
BUILTINS = {'documents_search', 'documents_report_gaps', 'code_reading', 'shell_sandbox',
            'ssh_logs', 'ssh_diagnostics', 'ssh_commands', 'service_lookup'}


def test_catalog_seeded_and_search_converted(store):
    with store.connect() as conn:
        ids = {row['id'] for row in conn.execute("SELECT id FROM ka.tools WHERE source='builtin'")}
        assert ids == BUILTINS
        assert all(row['usage'] for row in conn.execute("SELECT usage FROM ka.tools WHERE source='builtin'"))
        picks = {row['tool_id'] for row in conn.execute("SELECT tool_id FROM ka.agent_tools WHERE agent_id='search'")}
        assert picks == {'documents_search', 'documents_report_gaps', 'service_lookup'}
        operations = {row['tool_id'] for row in conn.execute("SELECT tool_id FROM ka.agent_tools WHERE agent_id='operations'")}
        assert 'ssh_commands' in operations
        assert conn.execute("SELECT count(*) AS n FROM ka.agent_tools WHERE agent_id='coordinator'").fetchone()['n'] == 0


def test_mcp_servers_get_tool_rows(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        server = conn.execute("""INSERT INTO ka.mcp_servers(project_id,name,description,url)
            VALUES (%s,'Grafana','dashboards','https://grafana.test/mcp') RETURNING id""", (project,)).fetchone()['id']
        row = conn.execute('SELECT id,source,name,description FROM ka.tools WHERE mcp_server_id=%s', (server,)).fetchone()
        assert row == {'id': f'mcp:{server}', 'source': 'mcp', 'name': 'Grafana', 'description': 'dashboards'}
        conn.execute("INSERT INTO ka.agent_tools(agent_id,tool_id) VALUES ('code',%s)", (f'mcp:{server}',))
        conn.execute('DELETE FROM ka.mcp_servers WHERE id=%s', (server,))
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_tools WHERE tool_id=%s', (f'mcp:{server}',)).fetchone()['n'] == 0


def test_ssh_actions_outcome_check(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("""INSERT INTO ka.ssh_actions(project_id,agent_id,function,arguments,outcome)
                VALUES (%s,'x','tail_log','{}','maybe')""", (project,))


def test_down_then_up(store):
    down = (MIGRATIONS / '0061_agent_tools.down.sql').read_text()
    up = (MIGRATIONS / '0061_agent_tools.up.sql').read_text()
    with store.connect() as conn:
        conn.execute(down, prepare=False)
        assert conn.execute("SELECT to_regclass('ka.agent_tools') AS t").fetchone()['t'] is None
        conn.execute(up, prepare=False)
        assert conn.execute("SELECT count(*) AS n FROM ka.tools WHERE source='builtin'").fetchone()['n'] == 8
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_agent_tools_migration.py -q`
Expected: FAIL — relation `ka.tools` does not exist.

- [ ] **Step 3: Write `0061_agent_tools.up.sql`**

Seeded usage texts must be identical to the `BUILTIN_TOOLS` usage strings in Task 2 (Task 2 has a test comparing them). Use dollar-quoting.

```sql
SET search_path = ka, public;

CREATE TABLE tools (
    id            text PRIMARY KEY CHECK (id ~ '^([a-z_]{1,40}|mcp:[0-9a-f-]{36})$'),
    source        text NOT NULL CHECK (source IN ('builtin','mcp')),
    mcp_server_id uuid UNIQUE REFERENCES mcp_servers(id) ON DELETE CASCADE,
    name          text NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
    description   text NOT NULL DEFAULT '' CHECK (length(description) <= 4000),
    usage         text NOT NULL DEFAULT '' CHECK (length(usage) <= 20000),
    revision      integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    updated_by    uuid REFERENCES principals(id),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    CHECK ((source = 'mcp') = (mcp_server_id IS NOT NULL))
);

CREATE TABLE agent_tools (
    agent_id text NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    tool_id  text NOT NULL REFERENCES tools(id) ON DELETE CASCADE,
    PRIMARY KEY (agent_id, tool_id)
);

CREATE TABLE ssh_actions (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_id   uuid NOT NULL REFERENCES projects(id),
    run_id       uuid,
    session_id   uuid,
    agent_id     text NOT NULL,
    server_id    uuid,
    actor_id     uuid REFERENCES principals(id),
    function     text NOT NULL,
    arguments    jsonb NOT NULL,
    outcome      text NOT NULL CHECK (outcome IN ('succeeded','failed','refused')),
    exit_code    integer,
    output_bytes integer,
    duration_ms  integer,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ssh_actions_recent ON ssh_actions(project_id, created_at DESC);

INSERT INTO tools(id, source, name, description, usage) VALUES
('documents_search','builtin','Documents search','Search and read the knowledge base.',
 $u$Use search_documents to find knowledge-base excerpts and read_document for a whole document.$u$),
('documents_report_gaps','builtin','Report documentation gaps','Answer only from documents and report what is missing.',
 $u$Answer only from excerpts returned by search_documents or read_document in this conversation. Search before answering any factual question. Cite every claim with its excerpt number in square brackets, such as [2]. Never cite a number that a tool did not return. Never use general knowledge or assumptions for factual claims. When the excerpts do not answer the question, call report_not_covered with what is missing, then tell the user that the documentation does not cover it and what is missing.$u$),
('code_reading','builtin','Code reading','Browse, search and analyse code projects.',
 $u$Use code_search to find a function or method by name and read its complete body, imports and call sites. Calls returned by code_search are syntax, including nested closures; resolve package/receiver targets before following them. Use ast_grep for custom structural patterns, classes and other declaration forms. Use search_files for text discovery and read_file for surrounding comments, imports and targeted ranges. When code_graph is available, use it first: search for symbols, code for full bodies, context for imports/types, and traverse for reachable calls; follow pagination and inspect edge metadata and unresolved references; graph paths are relative to CODE_ROOT; static edges are evidence, not proof of runtime execution.$u$),
('shell_sandbox','builtin','Shell sandbox','Run Bash in an isolated sandbox with read-only code.',
 $u$Use shell_run for Bash pipelines, rg, ast-grep and Python scripts. Selected code projects are mounted read-only at the same absolute paths; pwd shows writable scratch space that persists between tool calls within this run. Network access is disabled in the shell.$u$),
('ssh_logs','builtin','SSH logs','Read service, container and file logs on servers.',
 $u$Use service_logs, container_logs and tail_log with a server ID from your resources. Start with a narrow time range and few lines, then widen. Follow each server's instructions.$u$),
('ssh_diagnostics','builtin','SSH diagnostics','Read-only status, ports, disk, memory and logs on servers.',
 $u$Use the diagnostic functions with a server ID from your resources: service_status, container_list, disk_usage, memory, listening_ports and http_check, plus the log functions. They never change the server. Follow each server's instructions.$u$),
('ssh_commands','builtin','SSH commands','Run any command on servers (sudo only where allowed).',
 $u$Use run_command with a server ID from your resources. Follow each server's instructions. Request as_root only when the server's allow_sudo is true. Check exit_code.$u$),
('service_lookup','builtin','Service lookup','Look up services, deployments and dependencies.',
 $u$Use lookup_service with a service ID or name from your resources to see its deployments, servers, code projects and dependencies.$u$);

INSERT INTO tools(id, source, mcp_server_id, name, description)
SELECT 'mcp:' || id, 'mcp', id, left(name, 100), left(description, 4000) FROM mcp_servers;

CREATE FUNCTION sync_mcp_tool() RETURNS trigger LANGUAGE plpgsql SET search_path = ka, pg_temp AS $$
BEGIN
    INSERT INTO tools(id, source, mcp_server_id, name, description)
    VALUES ('mcp:' || NEW.id, 'mcp', NEW.id, left(NEW.name, 100), left(NEW.description, 4000))
    ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, description = EXCLUDED.description;
    RETURN NEW;
END $$;
CREATE TRIGGER mcp_servers_tool AFTER INSERT OR UPDATE OF name, description ON mcp_servers
    FOR EACH ROW EXECUTE FUNCTION sync_mcp_tool();

-- Convert capability switches into picks.
INSERT INTO agent_tools(agent_id, tool_id)
SELECT id, 'documents_search' FROM agents WHERE documents_enabled AND NOT is_coordinator
UNION SELECT id, 'documents_report_gaps' FROM agents WHERE documents_enabled AND documents_grounded AND NOT is_coordinator
UNION SELECT id, 'code_reading' FROM agents WHERE repositories_enabled AND NOT is_coordinator
UNION SELECT id, 'shell_sandbox' FROM agents WHERE repositories_enabled AND NOT is_coordinator
UNION SELECT id, 'ssh_commands' FROM agents WHERE ssh_enabled AND NOT is_coordinator
UNION SELECT id, 'service_lookup' FROM agents WHERE services_enabled AND NOT is_coordinator
UNION SELECT a.id, 'mcp:' || s.mcp_server_id FROM agents a
    JOIN agent_configs c ON c.agent = a.id AND c.server_id IS NULL
    JOIN agent_config_mcp_servers s ON s.config_id = c.id
    WHERE a.mcp_enabled AND NOT a.is_coordinator;
```

`0061_agent_tools.down.sql`:

```sql
SET search_path = ka, public;
DROP TRIGGER mcp_servers_tool ON mcp_servers;
DROP FUNCTION sync_mcp_tool();
DROP TABLE ssh_actions;
DROP TABLE agent_tools;
DROP TABLE tools;
```

(Task 7 extends both files.)

- [ ] **Step 4: Run tests**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_agent_tools_migration.py tests/test_reader_migration.py -q` then `make db-test` from the repo root (update `db/tests/run.sh`'s expected rollback version from the previous latest−1 to 60 if it hard-codes it).
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add db/migrations/0061_agent_tools.*.sql db/tests/run.sh services/admin-api/tests/test_agent_tools_migration.py
git commit -m "feat(db): tool catalog, agent tool picks and SSH audit table"
```

---

### Task 2: Catalog module, converter and new-shape `get_agent_config`

**Files:**
- Create: `services/admin-api/src/knowledge_admin/agent_tools.py`
- Modify: `services/admin-api/src/knowledge_admin/agents.py` (`get_agent_config` only)
- Test: `services/admin-api/tests/test_agent_tools.py`

**Interfaces:**
- Produces:
  - `BUILTIN_TOOLS: dict[str, dict]` — `{id: {'name', 'functions': tuple[str,...], 'resource': 'folders'|'code_projects'|'servers'|'services'|None, 'usage': str}}`
  - `SSH_TOOLS = ('ssh_logs','ssh_diagnostics','ssh_commands')`
  - `upgrade_snapshot(config: dict) -> dict` — idempotent; returns the new shape (never mutates the input)
  - `make_config(*, id, name, model, system_prompt, instructions='', tools=(), resources=None, description='', enabled=True, skills=None) -> dict`
  - `tool_ids(config) -> set[str]`, `has_any(config, *ids) -> bool`, `allowed_functions(config) -> set[str]`
  - New config shape:
    ```python
    {'id','name','description','when_to_use','enabled','revision','model','system_prompt','instructions',
     'settings_revision', 'config_version'?,
     'skills': {'enabled': bool, 'items': [...]},
     'tools': [{'id': str, 'name': str, 'usage': str, 'mcp_server': dict | None}],
     'resources': {'servers': [server], 'services': [service], 'folders': [folder],
                   'all_folders': bool, 'include_subfolders': bool,
                   'code_projects': [project], 'all_code_projects': bool, 'code_graphs': [graph]}}
    ```
    `server` keeps today's keys (`id, name, hostname, description, tags, ssh_allowed, server_revision, connection_revision, connection_source, username, port, allow_sudo, known_hosts, enabled, private_key_ref, config_revision, instructions, instructions_source`) plus `via_services: [service_id]`; the `skills*`/`mcp*` keys are gone. `mcp_server` has today's MCP keys (`id, name, description, url, enabled, tools, revision, bearer_token_ref`).

- [ ] **Step 1: Write the failing tests**

```python
"""Catalog constants, snapshot conversion and the new configuration shape."""
import copy

import pytest

from knowledge_admin import agent_tools
from knowledge_admin.agent_tools import BUILTIN_TOOLS, make_config, upgrade_snapshot
from knowledge_admin.agents import get_agent_config
from test_api import _client
from test_profiles import create
from test_repositories import HEADERS


def old(**blocks):
    base = {'id': 'a', 'name': 'A', 'description': '', 'enabled': True, 'instructions': '',
            'model': None, 'system_prompt': {'content': 'p'},
            'documents': {'enabled': False, 'folders': []}, 'code': {'enabled': False, 'projects': []},
            'ssh': {'enabled': False, 'servers': []}, 'mcp': {'enabled': False, 'servers': []},
            'skills': {'enabled': False, 'items': []}, 'services': {'enabled': False, 'items': []}}
    base.update(blocks)
    return base


def test_seeded_usage_matches_constants(store):
    with store.connect() as conn:
        rows = {r['id']: r['usage'] for r in conn.execute("SELECT id,usage FROM ka.tools WHERE source='builtin'")}
    assert rows == {tool_id: tool['usage'] for tool_id, tool in BUILTIN_TOOLS.items()}


def test_upgrade_snapshot_grounded_with_code():
    server = {'id': 's1', 'name': 'app', 'enabled': True, 'ssh_allowed': True, 'private_key_ref': {'id': 'k'},
              'skills': [], 'mcp_servers': [], 'instructions': 'be careful'}
    config = old(documents={'enabled': True, 'grounded': True, 'all_folders': False, 'include_subfolders': True,
                            'folders': [{'path': '/r/p', 'search_folder': 'p'}]},
                 code={'enabled': True, 'all_projects': False, 'projects': [{'id': 'c1', 'folder_paths': ['/c']}],
                       'graphs': [{'graph_path': '/g'}]},
                 ssh={'enabled': True, 'servers': [server]},
                 mcp={'enabled': True, 'servers': [{'id': 'm1', 'name': 'G', 'enabled': True, 'bearer_token_ref': None}]},
                 services={'enabled': True, 'items': [{'id': 'v1'}]})
    before = copy.deepcopy(config)
    new = upgrade_snapshot(config)
    assert config == before
    assert [t['id'] for t in new['tools']] == ['documents_search', 'documents_report_gaps', 'code_reading',
                                               'shell_sandbox', 'ssh_commands', 'service_lookup', 'mcp:m1']
    assert new['tools'][0]['usage'] == BUILTIN_TOOLS['documents_search']['usage']
    resources = new['resources']
    assert resources['folders'][0]['search_folder'] == 'p' and resources['all_folders'] is False
    assert resources['code_projects'][0]['id'] == 'c1' and resources['code_graphs'] == [{'graph_path': '/g'}]
    assert resources['servers'][0]['instructions'] == 'be careful' and 'mcp_servers' not in resources['servers'][0]
    assert resources['services'] == [{'id': 'v1'}]
    assert upgrade_snapshot(new) == new


def test_upgrade_snapshot_disabled_capabilities_keep_resources_but_no_tools():
    new = upgrade_snapshot(old(documents={'enabled': False, 'all_folders': True, 'include_subfolders': True, 'folders': []},
                               services={'enabled': False, 'items': [{'id': 'v1'}]}))
    assert new['tools'] == [] and new['resources']['all_folders'] is True and new['resources']['services'] == []


def test_make_config_and_helpers():
    config = make_config(id='doc', name='Doc', model=None, system_prompt={'content': 'x'},
                         tools=['code_reading', 'ssh_logs'], resources={'code_projects': [{'id': 'c'}]})
    assert agent_tools.tool_ids(config) == {'code_reading', 'ssh_logs'}
    assert agent_tools.has_any(config, *agent_tools.SSH_TOOLS)
    assert 'tail_log' in agent_tools.allowed_functions(config) and 'run_command' not in agent_tools.allowed_functions(config)
    assert config['resources']['servers'] == [] and config['skills'] == {'enabled': False, 'items': []}
    with pytest.raises(ValueError):
        make_config(id='x', name='x', model=None, system_prompt={'content': 'x'}, tools=['nope'])


def test_get_agent_config_new_shape_with_service_expansion(store, tmp_path, monkeypatch):
    monkeypatch.setenv('CODE_ROOT', str(tmp_path.resolve()))
    monkeypatch.setenv('RAG_SOURCE_ROOT', str(tmp_path.resolve()))
    client = _client(tmp_path, repository_store=store)
    agent = create(client, 'Docs maker')
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        server = conn.execute("""INSERT INTO ka.servers(project_id,name,hostname,ssh_allowed)
            VALUES (%s,'app-01','app-01.test',true) RETURNING id""", (project,)).fetchone()['id']
        service = conn.execute("""INSERT INTO ka.services(project_id,name) VALUES (%s,'payments') RETURNING id""", (project,)).fetchone()['id']
        conn.execute("""INSERT INTO ka.service_deployments(project_id,service_id,server_id,environment,runtime_name)
            VALUES (%s,%s,%s,'prod','payments')""", (project, service, server))
        conn.execute('INSERT INTO ka.agent_services(agent_id,service_id) VALUES (%s,%s)', (agent['id'], service))
        conn.execute("INSERT INTO ka.agent_tools(agent_id,tool_id) VALUES (%s,'ssh_logs'),(%s,'service_lookup')", (agent['id'], agent['id']))
    config = get_agent_config(agent['id'], conninfo=store.conninfo, working_copy=True)
    assert [t['id'] for t in config['tools']] == ['ssh_logs', 'service_lookup']
    assert config['tools'][0]['usage'] == BUILTIN_TOOLS['ssh_logs']['usage']
    servers = config['resources']['servers']
    assert [s['id'] for s in servers] == [str(server)] and servers[0]['via_services'] == [str(service)]
    assert [s['id'] for s in config['resources']['services']] == [str(service)]
    assert 'documents' not in config and 'ssh' not in config
```

Adjust the `ka.servers` / `ka.services` / `ka.service_deployments` INSERT column lists to the actual NOT NULL columns if they differ (check `db/migrations/0003_service_assets.up.sql`, `0004_server_inventory.up.sql`, `0050_service_environments.up.sql`); keep the asserted behaviour.

- [ ] **Step 2: Run to verify failure**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_agent_tools.py -q`
Expected: FAIL — `ModuleNotFoundError: knowledge_admin.agent_tools`.

- [ ] **Step 3: Implement `agent_tools.py`**

```python
"""The agent tool catalog and conversion of saved configurations into tools + resources."""
import copy
from typing import Any

from knowledge_admin import grounding

SSH_LOG_FUNCTIONS = ('service_logs', 'container_logs', 'tail_log')
BUILTIN_TOOLS: dict[str, dict[str, Any]] = {
    'documents_search': {'name': 'Documents search', 'functions': ('search_documents', 'read_document'), 'resource': 'folders',
        'usage': 'Use search_documents to find knowledge-base excerpts and read_document for a whole document.'},
    'documents_report_gaps': {'name': 'Report documentation gaps', 'functions': ('report_not_covered',), 'resource': None,
        'usage': grounding.POLICY.strip()},
    'code_reading': {'name': 'Code reading', 'resource': 'code_projects',
        'functions': ('list_files', 'read_file', 'search_files', 'code_search', 'ast_grep', 'code_graph'),
        'usage': 'Use code_search to find a function or method by name and read its complete body, imports and call sites. Calls returned by code_search are syntax, including nested closures; resolve package/receiver targets before following them. Use ast_grep for custom structural patterns, classes and other declaration forms. Use search_files for text discovery and read_file for surrounding comments, imports and targeted ranges. When code_graph is available, use it first: search for symbols, code for full bodies, context for imports/types, and traverse for reachable calls; follow pagination and inspect edge metadata and unresolved references; graph paths are relative to CODE_ROOT; static edges are evidence, not proof of runtime execution.'},
    'shell_sandbox': {'name': 'Shell sandbox', 'functions': ('shell_run',), 'resource': 'code_projects',
        'usage': 'Use shell_run for Bash pipelines, rg, ast-grep and Python scripts. Selected code projects are mounted read-only at the same absolute paths; pwd shows writable scratch space that persists between tool calls within this run. Network access is disabled in the shell.'},
    'ssh_logs': {'name': 'SSH logs', 'functions': SSH_LOG_FUNCTIONS, 'resource': 'servers',
        'usage': "Use service_logs, container_logs and tail_log with a server ID from your resources. Start with a narrow time range and few lines, then widen. Follow each server's instructions."},
    'ssh_diagnostics': {'name': 'SSH diagnostics', 'resource': 'servers',
        'functions': SSH_LOG_FUNCTIONS + ('service_status', 'container_list', 'disk_usage', 'memory', 'listening_ports', 'http_check'),
        'usage': "Use the diagnostic functions with a server ID from your resources: service_status, container_list, disk_usage, memory, listening_ports and http_check, plus the log functions. They never change the server. Follow each server's instructions."},
    'ssh_commands': {'name': 'SSH commands', 'functions': ('run_command',), 'resource': 'servers',
        'usage': "Use run_command with a server ID from your resources. Follow each server's instructions. Request as_root only when the server's allow_sudo is true. Check exit_code."},
    'service_lookup': {'name': 'Service lookup', 'functions': ('lookup_service',), 'resource': 'services',
        'usage': 'Use lookup_service with a service ID or name from your resources to see its deployments, servers, code projects and dependencies.'},
}
SSH_TOOLS = ('ssh_logs', 'ssh_diagnostics', 'ssh_commands')
EMPTY_RESOURCES = {'servers': [], 'services': [], 'folders': [], 'all_folders': False, 'include_subfolders': True,
                   'code_projects': [], 'all_code_projects': False, 'code_graphs': []}
_SERVER_DROP = ('skills', 'skills_source', 'mcp_servers', 'mcp_source')


def builtin(tool_id: str) -> dict[str, Any]:
    tool = BUILTIN_TOOLS[tool_id]
    return {'id': tool_id, 'name': tool['name'], 'usage': tool['usage'], 'mcp_server': None}


def mcp_tool(server: dict[str, Any]) -> dict[str, Any]:
    return {'id': f"mcp:{server['id']}", 'name': server['name'], 'usage': server.get('description', ''), 'mcp_server': server}


def upgrade_snapshot(config: dict[str, Any]) -> dict[str, Any]:
    """Return the tools + resources shape for any saved configuration; never mutates the input."""
    if 'tools' in config and 'resources' in config:
        return config
    old = copy.deepcopy(config)
    documents, code, ssh = old.get('documents', {}), old.get('code', {}), old.get('ssh', {})
    mcp, services = old.get('mcp', {}), old.get('services', {})
    tools = []
    if documents.get('enabled'):
        tools.append(builtin('documents_search'))
        if documents.get('grounded'):
            tools.append(builtin('documents_report_gaps'))
    if code.get('enabled'):
        tools += [builtin('code_reading'), builtin('shell_sandbox')]
    if ssh.get('enabled'):
        tools.append(builtin('ssh_commands'))
    if services.get('enabled'):
        tools.append(builtin('service_lookup'))
    if mcp.get('enabled'):
        servers = list(mcp.get('servers', []))
        if ssh.get('enabled'):
            for server in ssh.get('servers', []):
                servers += server.get('mcp_servers', [])
        seen = set()
        for server in servers:
            if server['id'] not in seen:
                seen.add(server['id'])
                tools.append(mcp_tool(server))
    new = {key: value for key, value in old.items() if key not in ('documents', 'code', 'ssh', 'mcp', 'services')}
    new['skills'] = old.get('skills', {'enabled': False, 'items': []})
    new['tools'] = tools
    new['resources'] = {
        'servers': [{k: v for k, v in server.items() if k not in _SERVER_DROP} | {'via_services': []}
                    for server in ssh.get('servers', [])],
        'services': services.get('items', []) if services.get('enabled') else [],
        'folders': documents.get('folders', []),
        'all_folders': bool(documents.get('all_folders', False)),
        'include_subfolders': bool(documents.get('include_subfolders', True)),
        'code_projects': code.get('projects', []),
        'all_code_projects': bool(code.get('all_projects', False)),
        'code_graphs': code.get('graphs', []),
    }
    return new


def make_config(*, id: str, name: str, model: dict | None, system_prompt: dict, instructions: str = '',
                tools=(), resources: dict | None = None, description: str = '', enabled: bool = True,
                skills: dict | None = None) -> dict[str, Any]:
    """Build a synthetic new-shape configuration for built-in workflows."""
    unknown = [tool for tool in tools if tool not in BUILTIN_TOOLS]
    if unknown:
        raise ValueError(f'Unknown built-in tools: {unknown}')
    return {'id': id, 'name': name, 'description': description, 'enabled': enabled, 'model': model,
            'system_prompt': system_prompt, 'instructions': instructions,
            'skills': skills or {'enabled': False, 'items': []},
            'tools': [builtin(tool) for tool in tools],
            'resources': {**copy.deepcopy(EMPTY_RESOURCES), **(resources or {})}}


def tool_ids(config: dict[str, Any]) -> set[str]:
    return {tool['id'] for tool in upgrade_snapshot(config)['tools']}


def has_any(config: dict[str, Any], *ids: str) -> bool:
    return bool(tool_ids(config) & set(ids))


def allowed_functions(config: dict[str, Any]) -> set[str]:
    return {fn for tool in tool_ids(config) if tool in BUILTIN_TOOLS for fn in BUILTIN_TOOLS[tool]['functions']}
```

These strings must stay identical to Task 1's seed (the seed-equality test enforces it).

- [ ] **Step 4: Rewrite `get_agent_config`'s non-version branch**

Keep the version branch but return `upgrade_snapshot(config)` there. For the working copy:

- Load picked tools: `SELECT t.id,t.name,t.usage,t.mcp_server_id FROM ka.agent_tools a JOIN ka.tools t ON t.id=a.tool_id WHERE a.agent_id=%s ORDER BY <catalog order: builtin ids in BUILTIN_TOOLS order, then mcp by lower(name)>`; MCP tool rows get `mcp_server` = the MCP server dict (same SELECT as today's `mcp_servers` query, with `bearer_token_ref`) and `usage` = the `ka.tools.usage` if non-empty else the MCP description.
- Folders and code projects: today's queries, unchanged except code projects add the selected services' `service_repositories` projects (not archived) with `via_services`.
- Services: today's `service_assets` query (no longer gated).
- Servers: today's server query over `operations_servers` UNION servers from the selected services' `service_deployments`, each with `via_services` (array of service ids, empty for directly selected); drop the per-server `skills*`/`mcp*` resolution. Keep `instructions`/`instructions_source`/connection fields.
- Skills: unchanged (`skills_agents`), `{'enabled': skills_enabled, 'items': [...]}`.
- Return the new shape (no `documents`/`code`/`ssh`/`mcp`/`services` keys).

- [ ] **Step 5: Run tests**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_agent_tools.py -q`
Expected: pass. (Other suites break until Task 3; that is expected and fixed there.)

- [ ] **Step 6: Commit**

```bash
git add services/admin-api/src/knowledge_admin/agent_tools.py services/admin-api/src/knowledge_admin/agents.py services/admin-api/tests/test_agent_tools.py
git commit -m "feat(agents): tool catalog, snapshot converter and tools+resources configuration"
```

---

### Task 3: Runtime on the new shape

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/agents.py` (`agent`, `create_agent`, `get_paths`, `get_ssh`, `get_mcp`, `get_tools`, `get_prompt`, `load_credentials`; remove `documents_grounded`)
- Modify: `services/admin-api/src/knowledge_admin/tools/files.py`, `tools/shell.py`, `tools/code_search.py`, `tools/ast_grep.py`, `tools/code_graph.py`, `tools/documents.py`
- Modify: `services/admin-api/src/knowledge_admin/board.py` (the `shell_enabled` copy at ~187-189 → use `agents.shell_enabled(config)`)
- Test: update `tests/test_sdk_agent.py`, `tests/test_agents.py`, `tests/test_function_tools.py`, `tests/test_document_tools.py`, `tests/test_board.py`, `tests/test_code_search.py`, `tests/test_ast_grep.py`, `tests/test_code_graph_sdk.py`; add `tests/test_agent_runtime.py`

**Interfaces:**
- Consumes: Task 2 `upgrade_snapshot`, `BUILTIN_TOOLS`, `tool_ids`, `allowed_functions`, `SSH_TOOLS`.
- Produces:
  - `agents.shell_enabled(config) -> bool` (`'shell_sandbox' in tool_ids`)
  - `get_ssh(config, credentials) -> list[server]` (resources servers that are enabled+ssh_allowed when any SSH tool is picked)
  - `get_mcp(config) -> list[mcp_server]` (from MCP tools, enabled only) — **signature drops `ssh_servers`**
  - `get_tools(config, ssh_servers) -> list[FunctionTool]` (functions of picked tools with something to work on; SSH functions from Task 4 are imported lazily — until Task 4 lands, map `ssh_commands` to the existing `ssh_run`)
  - `get_prompt(config, ssh_servers, selected_mcp) -> str`
  - Tool modules read `upgrade_snapshot(ctx.context['config'])` resources: code roots = `resources['code_projects'][*]['folder_paths']` when `code_reading` or `shell_sandbox` is picked; document folders = `resources['folders']` when `documents_search` is picked; graphs = `resources['code_graphs']`.

Rules (each needs a test in `tests/test_agent_runtime.py`):

- `agent()` and `create_agent()` call `config = upgrade_snapshot(config)` first; `context['shell_enabled'] = shell_enabled(config)`.
- `get_paths`: code project folder paths when `code_reading` or `shell_sandbox` is picked. Document folders are never mount-checked (only `documents_search`, which uses the RAG API, needs them).
- `get_tools`:
  - `documents_search` → `search_documents`, `read_document` when `resources['folders']` non-empty or `all_folders`;
  - `documents_report_gaps` → `report_not_covered`;
  - `code_reading` → `list_files`, `read_file`, `search_files`, `code_search`, `ast_grep` when code projects exist (or `all_code_projects`), plus `code_graph` when `code_graphs` non-empty;
  - `shell_sandbox` → `shell_run` when code projects exist;
  - SSH tools → their functions when `ssh_servers` non-empty;
  - `service_lookup` → `lookup_service` (Task 4) when services non-empty.
  The list has no duplicates (`ssh_logs` + `ssh_diagnostics` share functions).
- `get_prompt` order: system prompt (raise if blank) → `config['instructions']` → enabled skills block → for each picked tool, `usage` (MCP tools: usage or description) → resources JSON `{name, description, servers:[{id,name,hostname,port,username,description,tags,allow_sudo,instructions}], services, documents (folders), code_projects, code_graphs, mcp_servers:[{id,name,description}]}` including only non-empty entries → the existing three rule sentences ("Use only the configured resources and tools. …"). No tool-specific text outside `usage`.
- `load_credentials(store, config)`: `config = upgrade_snapshot(config)`; references = model key + SSH keys of `get_ssh`-eligible servers (when any SSH tool is picked) + bearer refs of enabled MCP tool servers; re-check agent enabled, model enabled, each eligible server still `ssh_allowed`, each MCP server enabled (as today).
- A pinned MCP tool whose `mcp_server` row was deleted: `load_credentials` raises `ValueError('A configured MCP server is unavailable or disabled.')` (existing message) — test `test_pinned_mcp_tool_for_deleted_server_is_reported`.
- `test_load_credentials_rechecks_effective_servers`: set `ssh_allowed=false` on a service-derived server after loading the config → `ValueError('A configured SSH server is unavailable or SSH is disabled.')`.

Test approach: existing tests that build old-shape configs keep working because entry points convert; update only assertions about tool names (`ssh_run` stays until Task 4), prompt text order, and `get_mcp` call sites. Add `tests/test_agent_runtime.py` with one test per rule above using `make_config(...)`.

- [ ] Steps: write `tests/test_agent_runtime.py` (failing) → run → implement → run `uv run --frozen pytest -q` for the whole admin-api suite → fix assertions in the listed test files where they encode the old prompt wording or tool gating → all green (except the known pre-existing failure) → commit `refactor(agents): run agents from tools and resources`.

---

### Task 4: SSH functions, service lookup and the SSH audit

**Files:**
- Create: `services/admin-api/src/knowledge_admin/tools/ssh_audit.py`, `services/admin-api/src/knowledge_admin/tools/inventory.py`
- Modify: `services/admin-api/src/knowledge_admin/tools/ssh.py` (replace `ssh_run` with the functions below), `agents.py` (`get_tools` mapping), `app.py` (`ssh_audit.configure(repository_store)`), `documentation_generation.py` `main()` (configure), `agent_sessions.py` `start_turn` and `board.py` (set `config['audit']` on the per-turn copy)
- Test: `services/admin-api/tests/test_ssh_functions.py`; update `tests/test_sdk_agent.py` (SSH cycle uses `run_command`), `tests/test_function_tools.py`

**Interfaces:**
- Produces:
  - `ssh_audit.configure(store) -> None`; `ssh_audit.record(*, config, function, arguments, server_id, outcome, exit_code=None, output_bytes=None, duration_ms=None) -> None` (no-op only when `configure` was never called **and** `PYTEST_CURRENT_TEST` is unset is NOT allowed: raise `RuntimeError('SSH audit is not configured')` when unconfigured, so production cannot silently skip)
  - `config['audit'] = {'project_id': str, 'actor_id': str|None, 'run_id': str|None, 'session_id': str|None}`
  - `build_command(function: str, args: dict, server: dict) -> str` (pure; raises `ValueError` with a model-facing message)
  - function tools: `service_status, service_logs, container_list, container_logs, tail_log, disk_usage, memory, listening_ports, http_check, run_command` (all `@function_tool`, first argument `server_id: str`), and `lookup_service(service: str)` in `inventory.py`.

- [ ] **Step 1: Write the failing tests**

```python
"""Typed SSH functions build safe commands, honour picked tools and are always audited."""
import asyncio
import json

import pytest
from agents.tool_context import ToolContext

from knowledge_admin.agent_tools import make_config
from knowledge_admin.tools import ssh as ssh_tools
from knowledge_admin.tools import ssh_audit
from knowledge_admin.tools.ssh import build_command

SERVER = {'id': 's1', 'name': 'app-01', 'hostname': 'app-01.test', 'port': 22, 'username': 'ops', 'known_hosts': '',
          'allow_sudo': False, 'enabled': True, 'ssh_allowed': True, 'private_key_ref': {'id': 'k'}, 'instructions': '',
          'log_locations': ['/srv/pay/log']}


@pytest.mark.parametrize('function,args,expected', [
    ('service_status', {'unit': 'nginx.service'}, "systemctl status nginx.service --no-pager"),
    ('service_logs', {'unit': 'pay@1', 'since': '2h', 'lines': 50}, "journalctl -u pay@1 --since -2h -n 50 --no-pager"),
    ('container_logs', {'container': 'payments', 'since': '30m', 'lines': 10}, "docker logs --since 30m --tail 10 payments"),
    ('disk_usage', {}, "df -hT -x tmpfs -x devtmpfs"),
    ('memory', {}, "free -m"),
    ('listening_ports', {}, "ss -tlnp"),
])
def test_build_command(function, args, expected):
    assert build_command(function, args, SERVER) == expected


@pytest.mark.parametrize('function,args', [
    ('service_status', {'unit': 'nginx; rm -rf /'}),
    ('service_logs', {'unit': 'x', 'since': '8d', 'lines': 10}),
    ('service_logs', {'unit': 'x', 'since': '1h', 'lines': 5000}),
    ('container_logs', {'container': '$(id)', 'since': '1h', 'lines': 10}),
    ('http_check', {'url': 'http://evil.test/'}),
    ('http_check', {'url': 'file:///etc/passwd'}),
])
def test_build_command_rejects_unsafe_arguments(function, args):
    with pytest.raises(ValueError):
        build_command(function, args, SERVER)


@pytest.mark.parametrize('path', ['/var/log/../../etc/shadow', 'var/log/syslog', '/etc/shadow', '/srv/pay/log/../../../etc/x'])
def test_tail_log_rejects_escaping_paths(path):
    with pytest.raises(ValueError):
        build_command('tail_log', {'path': path, 'lines': 10}, SERVER)


def test_tail_log_command_resolves_and_checks_roots():
    command = build_command('tail_log', {'path': '/srv/pay/log/app.log', 'lines': 20}, SERVER)
    assert 'readlink -f' in command and "'/var/log'" in command and "'/srv/pay/log'" in command and 'tail -n 20' in command


@pytest.fixture
def audit(monkeypatch):
    rows = []
    monkeypatch.setattr(ssh_audit, '_store', object())
    monkeypatch.setattr(ssh_audit, '_insert', lambda store, row: rows.append(row))
    return rows


def context(tools):
    config = make_config(id='log', name='Log agent', model=None, system_prompt={'content': 'x'}, tools=tools,
                         resources={'servers': [SERVER]})
    config['audit'] = {'project_id': 'p1', 'actor_id': 'u1', 'run_id': 'r1', 'session_id': None}
    return {'config': config, 'credentials': {'k': 'PRIVATE'}, 'workspace': None, 'shell_enabled': False}


def call(tool, ctx, **arguments):
    payload = json.dumps(arguments)
    return asyncio.run(tool.on_invoke_tool(ToolContext(context=ctx, tool_name=tool.name, tool_call_id='t',
                                                       tool_arguments=payload), payload))


def test_logs_only_agent_cannot_call_other_ssh_functions(audit, monkeypatch):
    ran = []
    async def fake_execute(connection, command, **kwargs):
        ran.append(command)
        return {'exit_code': 0, 'stdout': 'ok', 'stderr': ''}
    monkeypatch.setattr(ssh_tools, 'execute', fake_execute)
    ctx = context(['ssh_logs'])
    assert 'not available' in call(ssh_tools.run_command, ctx, server_id='s1', command='id')
    assert 'not available' in call(ssh_tools.service_status, ctx, server_id='s1', unit='nginx.service')
    assert 'ok' in call(ssh_tools.service_logs, ctx, server_id='s1', unit='nginx.service', since='1h', lines=10)
    assert ran == ['journalctl -u nginx.service --since -1h -n 10 --no-pager']
    assert [r['outcome'] for r in audit] == ['refused', 'refused', 'succeeded']
    assert audit[2]['actor_id'] == 'u1' and audit[2]['function'] == 'service_logs' and audit[2]['server_id'] == 's1'
    assert 'PRIVATE' not in json.dumps(audit)


def test_server_outside_resources_is_refused(audit):
    assert 'not available' in call(ssh_tools.service_logs, context(['ssh_logs']), server_id='other', unit='x', since='1h', lines=5)
    assert audit[0]['outcome'] == 'refused'


def test_unconfigured_audit_raises(monkeypatch):
    monkeypatch.setattr(ssh_audit, '_store', None)
    with pytest.raises(RuntimeError):
        ssh_audit.record(config={'audit': {}}, function='memory', arguments={}, server_id='s1', outcome='refused')
```

Add an integration test using the existing `ssh_server` fixture (`tests/test_ssh.py`) that runs `service_logs`-equivalent `memory` against the fixture host through `call(...)` and checks a `succeeded` row written by the real `_insert` into `ka.ssh_actions` (configure with the `store` fixture).

- [ ] **Step 2: Run to verify failure** — `uv run --frozen pytest tests/test_ssh_functions.py -q` → ImportError.

- [ ] **Step 3: Implement**

`tools/ssh_audit.py`:

```python
"""Durable, insert-only record of every SSH function call."""
import json
from typing import Any

from psycopg.types.json import Jsonb

_store = None


def configure(store) -> None:
    global _store
    _store = store


def _insert(store, row: dict[str, Any]) -> None:
    with store.connect() as conn:
        conn.execute('''INSERT INTO ka.ssh_actions(project_id,run_id,session_id,agent_id,server_id,actor_id,function,
            arguments,outcome,exit_code,output_bytes,duration_ms) VALUES (%(project_id)s,%(run_id)s,%(session_id)s,
            %(agent_id)s,%(server_id)s,%(actor_id)s,%(function)s,%(arguments)s,%(outcome)s,%(exit_code)s,
            %(output_bytes)s,%(duration_ms)s)''', {**row, 'arguments': Jsonb(row['arguments'])})


def record(*, config: dict, function: str, arguments: dict, server_id: str | None, outcome: str,
           exit_code: int | None = None, output_bytes: int | None = None, duration_ms: int | None = None) -> None:
    if _store is None:
        raise RuntimeError('SSH audit is not configured')
    audit = config.get('audit') or {}
    _insert(_store, {'project_id': audit.get('project_id'), 'run_id': audit.get('run_id'),
                     'session_id': audit.get('session_id'), 'agent_id': config['id'], 'server_id': server_id,
                     'actor_id': audit.get('actor_id'), 'function': function, 'arguments': arguments,
                     'outcome': outcome, 'exit_code': exit_code, 'output_bytes': output_bytes, 'duration_ms': duration_ms})
```

If `config['audit']['project_id']` is missing (e.g. a synthetic config without audit), `_insert` must still succeed: resolve it in `record` with `store.project(conn, <the single app name>)` — add `configure(store, app_name)` storing both, and use it when `project_id` is absent.

`tools/ssh.py` — keep the module docstring; implement:

```python
UNIT = re.compile(r'^[A-Za-z0-9@._:-]{1,200}$')
SINCE = re.compile(r'^([1-9][0-9]{0,3})([mhd])$')
MAX_SINCE_MINUTES = 7 * 24 * 60

def _since(value: str) -> str:
    match = SINCE.fullmatch(value or '')
    if not match:
        raise ValueError('Use since like 30m, 2h or 1d (at most 7d).')
    minutes = int(match[1]) * {'m': 1, 'h': 60, 'd': 1440}[match[2]]
    if minutes > MAX_SINCE_MINUTES:
        raise ValueError('Use since like 30m, 2h or 1d (at most 7d).')
    return value

def _lines(value: int) -> int:
    if not isinstance(value, int) or not 1 <= value <= 2000:
        raise ValueError('Use lines from 1 to 2000.')
    return value

def _name(value: str, label: str) -> str:
    if not UNIT.fullmatch(value or ''):
        raise ValueError(f'Use a plain {label} name.')
    return value

def log_roots(server: dict) -> list[str]:
    return ['/var/log', *server.get('log_locations', [])]

def build_command(function: str, args: dict, server: dict) -> str:
    q = shlex.quote
    if function == 'service_status':
        return f"systemctl status {q(_name(args['unit'], 'unit'))} --no-pager"
    if function == 'service_logs':
        return f"journalctl -u {q(_name(args['unit'], 'unit'))} --since -{_since(args['since'])} -n {_lines(args['lines'])} --no-pager"
    if function == 'container_list':
        return "docker ps --format '{{.Names}}\\t{{.Image}}\\t{{.Status}}'"
    if function == 'container_logs':
        return f"docker logs --since {_since(args['since'])} --tail {_lines(args['lines'])} {q(_name(args['container'], 'container'))}"
    if function == 'tail_log':
        path = args.get('path') or ''
        roots = log_roots(server)
        if not path.startswith('/') or '..' in path.split('/') or '\0' in path or not any(
                path == root or path.startswith(root.rstrip('/') + '/') for root in roots):
            raise ValueError('Use an absolute log path under /var/log or a log location of this server.')
        allowed = ' '.join(q(root) for root in roots)
        return (f"p=$(readlink -f -- {q(path)}) && for r in {allowed}; do r=$(readlink -f -- \"$r\"); "
                f"case \"$p\" in \"$r\"/*) exec tail -n {_lines(args['lines'])} -- \"$p\";; esac; done; "
                f"echo 'Path is outside the allowed log locations.' >&2; exit 3")
    if function == 'disk_usage':
        return 'df -hT -x tmpfs -x devtmpfs'
    if function == 'memory':
        return 'free -m'
    if function == 'listening_ports':
        return 'ss -tlnp'
    if function == 'http_check':
        url = urlsplit(args.get('url') or '')
        if url.scheme not in ('http', 'https') or url.hostname not in (server['hostname'], 'localhost', '127.0.0.1'):
            raise ValueError("Check only this server's own http(s) address, localhost or 127.0.0.1.")
        return f"curl -sS -o /dev/null -w '%{{http_code}} %{{time_total}}' --max-time 10 {q(args['url'])}"
    raise ValueError('Unknown SSH function.')
```

(Remove the `log_locations` key handling if not available from the server dict: in Task 2's server query add `log_locations` = array of `service_deployments.log_location` on that server that are non-null absolute paths.)

One shared runner used by every function tool:

```python
async def _run(ctx, function: str, server_id: str, args: dict, *, as_root: bool = False, raw: str | None = None,
               seconds: int = 60) -> str:
    config = upgrade_snapshot(ctx.context['config'])
    audit = dict(function=function, arguments={k: v for k, v in args.items()}, server_id=server_id)
    server = next((s for s in get_ssh(config, ctx.context['credentials']) if s['id'] == server_id), None)
    if function not in allowed_functions(config) or server is None:
        ssh_audit.record(config=config, outcome='refused', **audit)
        return 'This function or server is not available to this agent.'
    try:
        command = raw if raw is not None else build_command(function, args, server)
    except ValueError as exc:
        ssh_audit.record(config=config, outcome='refused', **audit)
        return str(exc)
    if as_root and not server['allow_sudo']:
        ssh_audit.record(config=config, outcome='refused', **audit)
        return 'Root is not allowed on this server.'
    connection = SSHConnection(hostname=server['hostname'], port=server['port'], username=server['username'],
        known_hosts=server['known_hosts'], private_key=ctx.context['credentials'][server['private_key_ref']['id']],
        allow_sudo=server['allow_sudo'])
    started = time.monotonic()
    try:
        result = await execute(connection, command, as_root=as_root, seconds=seconds)
    except SSHError as exc:
        ssh_audit.record(config=config, outcome='failed', duration_ms=int((time.monotonic()-started)*1000), **audit)
        return redact(str(exc), ctx.context['credentials'].values())
    ssh_audit.record(config=config, outcome='succeeded', exit_code=result['exit_code'],
                     output_bytes=len(result['stdout']) + len(result['stderr']),
                     duration_ms=int((time.monotonic()-started)*1000), **audit)
    return redact(json.dumps({'server_id': server_id, **result}), ctx.context['credentials'].values())
```

Each `@function_tool` is a thin wrapper with a docstring and typed args, e.g.:

```python
@function_tool
async def service_logs(ctx: RunContextWrapper[AgentContext], server_id: str, unit: str, since: str = '1h', lines: int = 200) -> str:
    """Read a systemd unit's journal on a server from your resources.

    Args:
        server_id: Server ID from your resources.
        unit: systemd unit name, e.g. nginx.service.
        since: How far back, e.g. 30m, 2h, 1d (at most 7d).
        lines: Number of lines, 1-2000.
    """
    return await _run(ctx, 'service_logs', server_id, {'unit': unit, 'since': since, 'lines': lines})
```

`run_command(ctx, server_id, command, as_root=False, timeout_seconds=60)` validates like today's `ssh_run` (non-empty, no NUL, ≤ 20000, timeout 1–300) and calls `_run(ctx, 'run_command', server_id, {'command': command, 'as_root': as_root}, as_root=as_root, raw=command, seconds=timeout_seconds)`.

`tools/inventory.py` — `lookup_service(ctx, service: str)`: refuse unless `service_lookup` is picked; match by id or case-insensitive name within `resources['services']`; return JSON of `{id, name, description, owner, operations_notes, deployments (server name/hostname, environment, runtime, runtime_name, port, log_location, deployed_version), repositories (name, remote_url, current_commit), dependencies, dependents}`; unknown → "This service is not in your resources."

Wire `get_tools` (Task 3) to these functions, `app.create_app` → `ssh_audit.configure(repository_store, cfg.apps[0].name)` when a store exists, documentation worker `main()` likewise. In `start_turn` (after `config` is final, on the copy passed to `agent`), set `config['audit'] = {'project_id': str(project), 'actor_id': str(actor_id), 'run_id': str(value.id), 'session_id': str(session_id)}`; board sets the same with the board task's requester and task id as `run_id`.

- [ ] **Step 4: Run** `uv run --frozen pytest tests/test_ssh_functions.py tests/test_sdk_agent.py tests/test_function_tools.py tests/test_agent_runs.py tests/test_board.py -q` → pass.

- [ ] **Step 5: Commit** `feat(agents): typed SSH functions, service lookup and SSH audit`

---

### Task 5: Callers that read configuration shapes

**Files:**
- Modify: `reader_policy.py`, `reader_api.py` (`narrow`, transcript), `reader_settings.py`, `agent_versions.py` (server instruction edits), `agent_sessions.py` (tuning reviewer), `profiles_api.py` (list overrides from versions), `documentation_generation.py` and `documentation_updates.py` (synthetic configs via `make_config`), `agent_wizard.py` (designer config via `make_config`), `scripts/benchmarks/documentation_commit_update.py`, `scripts/benchmarks/rlm_handler.py`, `scripts/benchmarks/api_handler_step.py`
- Test: update `tests/test_reader_settings.py`, `tests/test_reader_api.py`, `tests/test_agent_versions.py`, `tests/test_agent_sessions.py`, `tests/test_documentation_generation.py`, `tests/test_code_documentation_phases.py`, `tests/test_documentation_updates.py`, `tests/test_agent_wizard.py`

**Interfaces / rules (each with a test):**

- `knowledge_violations(config)`: `config = upgrade_snapshot(config)`; `ids = tool_ids(config)`; reasons in this order:
  - not enabled → "agent is disabled"; no enabled model → "no enabled model";
  - any `ssh_*` → "uses SSH"; `code_reading` → "uses code"; `shell_sandbox` → "uses the shell"; any `mcp:` → "uses MCP tools";
  - missing `documents_search` or `documents_report_gaps` → "document search with gap reporting is off".
  `reader_settings.violations` keeps the "active version vN predates these settings — save and activate a new version" hint.
- `reader_api.narrow(config, folders, include_subfolders)`: works on `upgrade_snapshot(config)['resources']` (`folders`, `all_folders` → False); transcript reads `resources['all_folders'|'include_subfolders'|'folders']`.
- Tuning reviewer (`agent_sessions.py` ~393-399): replace the capability loop with `reviewer = upgrade_snapshot(copy.deepcopy(config)); reviewer['tools'] = []`.
- `agent_versions.py` (~130-138): per-server instruction edits apply to `upgrade_snapshot(source)['resources']['servers']`; drafts store the new shape.
- `profiles_api.py` listing (~109-118): derive the displayed tools from `upgrade_snapshot(saved.config)['tools']` and return `tool_ids` instead of capability flags.
- Documentation generation (`documentation_generation.py` ~474-484): `make_config(id=f'documentation-{unit.kind}', name=f'Documentation: {unit.kind}', description='Built-in documentation step', model=model, system_prompt={'content': row['content']}, tools=(['code_reading','shell_sandbox'] if reads_code else []) + (['ssh_commands'] if uses_ssh else []), resources={'code_projects': code_projects if reads_code else [], 'code_graphs': graphs if reads_code else [], 'servers': servers if uses_ssh else []})`. Update the later mutations: `config['resources']['code_graphs']`/`['code_projects']` (~561-570), `config['tools'] = [t for t in config['tools'] if not t['id'].startswith('ssh_')]` (~607), and the writing phase removes code tools (~655). Resumed jobs load `agent_snapshot` through `upgrade_snapshot`.
- `documentation_updates.py` (~291-301), `agent_wizard.py` (~191-200) and the three benchmark scripts: build with `make_config`.

- [ ] Steps: update tests first (failing) → implement → run `uv run --frozen pytest -q` (admin-api) → commit `refactor(agents): callers use tools and resources`.

---

### Task 6: APIs — agent tools, tool catalog, resources, SSH actions, wizard, profiles compatibility

**Files:**
- Create: `services/admin-api/src/knowledge_admin/tools_api.py`
- Modify: `profiles_api.py` (input/update/create/delete), `api.py` (router), `agent_wizard.py` (proposal + create), `operations_store.py` (`save_settings` no longer writes MCP or per-server skills/MCP; per-server config keeps instructions only), `operations_api.py` (per-server config input loses `skill_ids`/`mcp_server_ids`/inherit flags), `mcp_api.py` (remove `GET/PUT /mcp/code`), `packages/llm-settings/src/knowledge_llm/profiles.py` (`profile()` compatibility fields)
- Test: `services/admin-api/tests/test_tools_api.py`; update `tests/test_profiles.py`, `tests/test_agents.py`, `tests/test_agent_wizard.py`, `tests/test_operations_configs.py`, `tests/test_mcp.py`, `tests/test_code_agent.py`; code-agent `tests/test_profiles.py` stays green

**Interfaces:**

| Endpoint | Behaviour |
| --- | --- |
| `GET /api/tools` | `{tools: [{id, source, name, description, usage, revision, functions: [..] (builtin) or mcp tool names (mcp), mcp_server_id}]}` in catalog order |
| `PUT /api/tools/{id}` | `{usage (≤20000), description (≤4000), revision}` → updated row; 409 on stale revision; audit `tool.updated`; MCP rows editable too |
| `PUT /api/agents/{id}` | gains `tool_ids: list[str] (≤ 50, unique)`; unknown or MCP ids of disabled/untested servers → 400 "Select available tools."; coordinator with any tool → 400 "Configure tools on the specialists the coordinator delegates to."; capability fields removed (`documents_enabled`, `documents_grounded`, `repositories_enabled`, `ssh_enabled`, `mcp_enabled`, `services_enabled` → 400 extra input); `skills_enabled`, folders, projects, services, `all_*`, `include_subfolders` unchanged |
| `GET /api/agents/{id}` | adds `tool_ids` |
| `GET /api/agents/{id}/resources` | `{selected: {server_ids, service_ids, folder_ids, repository_ids, all_folders, all_repositories, include_subfolders}, effective: {servers: [{id,name,hostname,via_services}], code_projects: [{id,name,via_services}]}}` |
| `GET /api/ssh-actions` | `{actions: [...], next: id|null}` newest first, filters `server_id`, `agent_id`, `actor` (email), `before` (id), `limit` ≤ 200; joins principal email and server name |

- MCP selection rule (from `mcp_settings.save_selection`): an MCP tool can be newly picked only if its server is enabled, tested and has tools.
- Wizard: `AgentProposal` replaces capability flags with `tool_ids`; per-server `skill_ids`/`mcp_server_ids` removed; creation inserts `ka.agent_tools` rows with the same MCP rule (fixing the bypass).
- `knowledge_llm.profile()` adds `row['tool_ids']` and derived `row['documents_enabled'] = 'documents_search' in tool_ids`, `row['repositories_enabled'] = 'code_reading' in tool_ids` (reading `ka.agent_tools` when the table exists), so docs-wiki and code-agent are unchanged.

- [ ] Steps: write `tests/test_tools_api.py` covering every row of the table and the MCP rule (failing) → implement → update the listed tests → run admin-api, code-agent and docs-wiki suites → commit `feat(admin): tool catalog, agent tool picks, resources and SSH actions APIs`.

---

### Task 7: Finish migration `0061` — drop the capability model

**Files:**
- Modify: `db/migrations/0061_agent_tools.up.sql` (append), `0061_agent_tools.down.sql` (prepend restore)
- Modify: `agents.py`, `operations_store.py`, `profiles_api.py`, `agent_wizard.py` — delete remaining reads/writes of the dropped columns and tables
- Test: extend `tests/test_agent_tools_migration.py`; `tests/test_reader_migration.py` (expectations on dropped columns move to tools)

Append to the up migration (after the conversion):

```sql
DO $$
DECLARE offenders text;
BEGIN
    SELECT string_agg(DISTINCT agent, ', ') INTO offenders FROM agent_configs
     WHERE server_id IS NOT NULL AND (NOT inherit_skills OR NOT inherit_mcp_servers);
    IF offenders IS NOT NULL THEN
        RAISE EXCEPTION 'Remove per-server skill/MCP overrides first (agents: %)', offenders;
    END IF;
END $$;

ALTER TABLE agents DROP CONSTRAINT coordinator_delegates_resources;
ALTER TABLE agents DROP COLUMN documents_enabled, DROP COLUMN documents_grounded, DROP COLUMN repositories_enabled,
    DROP COLUMN ssh_enabled, DROP COLUMN mcp_enabled, DROP COLUMN services_enabled;
DROP TABLE agent_config_skills;
DROP TABLE agent_config_mcp_servers;
ALTER TABLE agent_configs DROP COLUMN inherit_skills, DROP COLUMN inherit_mcp_servers;
```

PostgreSQL drops CHECK constraints that involve a dropped column automatically, so no explicit constraint drops are needed for the `inherit_*` columns.

Prepend to the down migration (before dropping the new tables) the restore:

```sql
SET search_path = ka, public;
ALTER TABLE agent_configs ADD COLUMN inherit_skills boolean NOT NULL DEFAULT true,
    ADD COLUMN inherit_mcp_servers boolean NOT NULL DEFAULT true;
UPDATE agent_configs SET inherit_skills = false, inherit_mcp_servers = false WHERE server_id IS NULL;
UPDATE agent_configs SET instructions = '' WHERE server_id IS NULL AND instructions IS NULL;
ALTER TABLE agent_configs ADD CHECK (server_id IS NOT NULL OR (instructions IS NOT NULL AND NOT inherit_skills)),
    ADD CHECK (server_id IS NOT NULL OR NOT inherit_mcp_servers);
CREATE TABLE agent_config_skills (
    config_id uuid NOT NULL,
    project_id uuid NOT NULL,
    skill_id uuid NOT NULL,
    PRIMARY KEY (config_id, skill_id),
    FOREIGN KEY (config_id, project_id) REFERENCES agent_configs(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (skill_id, project_id) REFERENCES agent_skills(id, project_id) ON DELETE CASCADE
);
CREATE TABLE agent_config_mcp_servers (
    config_id uuid NOT NULL,
    project_id uuid NOT NULL,
    mcp_server_id uuid NOT NULL,
    PRIMARY KEY (config_id, mcp_server_id),
    FOREIGN KEY (config_id, project_id) REFERENCES agent_configs(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (mcp_server_id, project_id) REFERENCES mcp_servers(id, project_id) ON DELETE CASCADE
);
ALTER TABLE agents ADD COLUMN documents_enabled boolean NOT NULL DEFAULT false,
    ADD COLUMN documents_grounded boolean NOT NULL DEFAULT false,
    ADD COLUMN repositories_enabled boolean NOT NULL DEFAULT false,
    ADD COLUMN ssh_enabled boolean NOT NULL DEFAULT false,
    ADD COLUMN mcp_enabled boolean NOT NULL DEFAULT false,
    ADD COLUMN services_enabled boolean NOT NULL DEFAULT false;
UPDATE agents a SET
    documents_enabled = EXISTS (SELECT 1 FROM agent_tools t WHERE t.agent_id=a.id AND t.tool_id='documents_search'),
    documents_grounded = EXISTS (SELECT 1 FROM agent_tools t WHERE t.agent_id=a.id AND t.tool_id='documents_report_gaps'),
    repositories_enabled = EXISTS (SELECT 1 FROM agent_tools t WHERE t.agent_id=a.id AND t.tool_id IN ('code_reading','shell_sandbox')),
    ssh_enabled = EXISTS (SELECT 1 FROM agent_tools t WHERE t.agent_id=a.id AND t.tool_id LIKE 'ssh\_%'),
    mcp_enabled = EXISTS (SELECT 1 FROM agent_tools t WHERE t.agent_id=a.id AND t.tool_id LIKE 'mcp:%'),
    services_enabled = EXISTS (SELECT 1 FROM agent_tools t WHERE t.agent_id=a.id AND t.tool_id='service_lookup');
INSERT INTO agent_config_mcp_servers(config_id, mcp_server_id, project_id)
SELECT c.id, tl.mcp_server_id, c.project_id FROM agent_tools t JOIN tools tl ON tl.id=t.tool_id
  JOIN agent_configs c ON c.agent=t.agent_id AND c.server_id IS NULL WHERE tl.source='mcp';
ALTER TABLE agents ADD CONSTRAINT coordinator_delegates_resources CHECK (
    NOT is_coordinator OR NOT (documents_enabled OR repositories_enabled OR ssh_enabled
        OR skills_enabled OR mcp_enabled OR services_enabled)
);
```

Tests to add: up drops the columns/tables; a database with a per-server override fails the up migration with the message; down restores flags from picks (`documents_report_gaps` → `documents_grounded`, `ssh_logs` → `ssh_enabled`) and agent-level MCP selections.

- [ ] Steps: tests (failing) → migration edits → remove dead code paths → `make db-test` and full admin-api suite → commit `feat(db): drop capability switches and per-server overrides`.

---

### Task 8: Admin UI — Tools and Resources tabs

**Files:**
- Modify: `services/admin-ui/app/lib/profiles.ts` (types: remove capability fields, add `tool_ids`; `saveAgent` payload), `app/components/AgentDetailView.tsx` (tabs), `app/components/AgentProfileSettings.tsx` (General + Skills only; folders/projects/services move), `app/components/OperationsView.tsx` (servers section only: allowed servers, per-server instructions, Configure SSH connection; remove inherit/skills/MCP overrides), `app/components/AgentsView.tsx` (tools column)
- Create: `app/lib/tools.ts` (`getTools`, `saveTool`, `getAgentResources`), `app/components/AgentToolsTab.tsx`, `app/components/AgentResourcesTab.tsx`
- Test: `app/components/__tests__/agentTools.test.tsx`; update `agentTabs.test.tsx`, `operations.test.tsx`, `agents.test.tsx`

**Behaviour:**

- Non-coordinator tabs: `general, skills, tools, resources, prompts, versions, test` (coordinator: `general, prompts`). Old `?view=documents|git|ssh|mcp|services` links redirect to `resources` (or `tools` for `mcp`).
- **Tools tab:** checklist grouped Documents (`documents_search`, `documents_report_gaps`), Code (`code_reading`, `shell_sandbox`), SSH (`ssh_logs`, `ssh_diagnostics`, `ssh_commands`), Inventory (`service_lookup`), MCP servers (each `mcp:` tool; not-ready servers disabled with "Test the connection on MCP servers first"). Each row: name, description, expandable usage preview, link "Edit usage" → `/tools#<id>`. A hint under SSH: "SSH logs is safe for lower-privileged users; SSH commands can change servers." Save → `PUT /api/agents/{id}` with `tool_ids`.
- **Resources tab:** sections Servers (existing allowed-servers checklist + per-server instructions + Configure SSH connection, from `OperationsView`), Services (checklist; under each selected service list "brings in: app-01, app-02 · payments-api" from `GET /api/agents/{id}/resources`), Document folders (all/specific + include subfolders), Code projects (all/specific). Warnings: "SSH tools are picked but there are no servers" when an SSH tool is picked and effective servers are empty; similarly for code and documents.
- Agents list column header "Tools", badges with tool names (MCP: server name).

Tests: tab list; picking/unpicking tools saves `tool_ids`; MCP not-ready disabled; resources service expansion text; warnings; legacy `?view=ssh` redirect.

- [ ] Steps: tests (failing) → implement → `pnpm test` + typecheck (`pnpm exec react-router typegen && pnpm exec tsc --noEmit`) → commit `feat(admin-ui): Tools and Resources tabs`.

---

### Task 9: Admin UI — Tools page, SSH actions, wizard, versions, cleanup

**Files:**
- Create: `app/routes/tools.tsx`, `app/components/ToolsView.tsx`, `app/components/SshActionsView.tsx`, `app/routes/sshActions.tsx`
- Modify: `app/routes.ts` (`tools`, `activity/ssh`), `app/components/AdminLayout.tsx` (nav: Administration → Tools; Activity page gets a "SSH actions" link/tab), `app/components/AgentWizard.tsx` + `app/lib/agentWizard.ts` (tools + resources instead of capability tabs; per-server skills/MCP removed), `app/components/AgentVersionsView.tsx` + `app/lib/agentVersions.ts` (`resources.servers` instead of `ssh.servers`), `app/components/ReaderAssistantCard.tsx` (copy: "It may only use Documents search, Report documentation gaps and Service lookup."), `app/components/AgentTestView.tsx` (activity labels for new SSH function names; no change to commands rendering)
- Delete: `app/components/CodeMCPSettings.tsx`, `SearchAgentView.tsx`, `CodeSearchAgentView.tsx` and their tests/mocks (`mcp.test.tsx` parts for CodeMCPSettings, `codeSearchAgent.test.tsx`, mocks in `agentTabs.test.tsx`); `lib/mcp.ts` `getCodeMCP`/`saveCodeMCP`
- Test: `__tests__/toolsPage.test.tsx`, `__tests__/sshActions.test.tsx`; update `agentWizard.test.tsx`, `agentVersions.test.tsx`, `readerAssistant.test.tsx`, `mcp.test.tsx`

**Behaviour:**

- **Tools page:** table of tools (name, source badge Built-in/MCP, functions); selecting a row opens an editor for description and usage (Markdown, preview), Save with revision conflict message, anchor `#<id>` opens that tool.
- **SSH actions:** table time, user, agent, server, function, arguments (compact), outcome badge, exit code, duration; filters server/agent/user; "Older" paging via `before`.
- **Wizard:** proposal tabs General, Prompt, Tools, Resources; creation sends `tool_ids` and resources.
- **Versions:** per-server instruction editing and config preview use `resources.servers`.

- [ ] Steps: tests (failing) → implement → `pnpm test` + typecheck → commit `feat(admin-ui): Tools page, SSH actions and wizard on tools`.

---

### Task 10: Documentation

**Files:** `docs/user-guide.md` (Agents section: Tools and Resources tabs, Tools page, SSH actions; Appendix A tool table; Reader rule; upgrade note: capability switches became tools, per-server skill/MCP overrides removed, `ssh_run` is now `run_command`), `docs/agent-profiles.md`, `docs/agents.md`, `services/admin-api/README.md`, `services/admin-api/src/knowledge_admin/tools/README.md`, `README.md` (one line).

- [ ] Steps: edit docs to match the implemented behaviour → `rg -n "documents_enabled|ssh_enabled|repositories_enabled|ssh_run|Answer only from documents" docs services/*/README.md README.md` returns only intentional historical mentions → commit `docs: agent tools and resources`.
