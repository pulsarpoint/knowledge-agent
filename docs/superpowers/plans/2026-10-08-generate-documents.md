# Knowledge Agents Step 4 — Generate Documents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Documentation is generated only with an agent. **Docs → Generate documents** lists the generation sessions;
**New** opens one form: **Agent** (enabled Claude Code agents), **What to document** (Code / Environment / Services /
Servers, each with its own picker, at least one item), **Instructions** (optional), **Document name** and **folder**.
There is no model or prompt field: the agent runs with its own model, prompt, instructions, skills and Claude Code
tools. The form's only products are the first message ("Document the following." with `## Code`, `## Environments`,
`## Services`, `## Servers` and `## Instructions` sections) and read access: chosen code projects and the code projects
linked to chosen environments and services become working folders; environments, services and servers are described as
text. Submitting creates a session and opens it at `/documentation/generations/<id>`. The separate **Sessions** menu
item goes away and `/documentation/sessions*` redirects. The old one-shot generation (service, whole environment and
code project scopes, saved workflows, phases, fixed agent snapshots, the documentation worker) is removed completely,
with its tables and history; saved documents and their exported files stay. The session page fits the screen: Activity
and Draft scroll on their own, Activity follows new events at the bottom (**Jump to latest** when you scroll up),
opening loads only the latest 100 events and older pages load near the top until **Beginning of the session**.

**Architecture:**
- **Schema (migration 0073 `remove_documentation_generation`):** adds `ka.documents.phase_metadata` (the service phase
  export layout, copied from each document's latest generation job); drops the `document_versions → documentation_generations`
  foreign key (versions keep `origin` and `generation_id` as history); drops `documentation_workflow_details`,
  `documentation_workflow_jobs`, `documentation_workflows`, `documentation_generations` and `check_documentation_job()`;
  deletes `documentation_generate` jobs with their job events and questions, and the run records with run keys `doc:*`;
  removes `documentation_generate` from the `jobs` checks; replaces `seed_api_documentation_prompt(uuid)` without its
  update of workflow drafts. The down migration restores the empty schema (not the history).
- **admin-api:** `documentation_generation.py`, `documentation_workflows.py`, `documentation_updates.py`,
  `api_evidence.py`, their routes (`/api/documentation/generations*`, `/api/documentation/workflows*`), the
  `knowledge-doc-worker` script and the `documentation-worker` compose service go. `documents.export_input` reads
  `documents.phase_metadata`. A new module `generate_documents.py` turns the form into the session's context snapshot
  (`subjects`) and first message (`first_message`). `POST /api/claude-code/sessions` takes `agent_id`,
  `code_project_ids`, `environment_ids`, `service_ids`, `server_ids`, `instructions`, `document_name`, `folder` (extra
  fields such as `model_id`, `prompt_id`, `comments`, `folders`, `notes` are refused); the model is the agent's model.
  `GET /api/claude-code/sessions/{id}/events/history?before=<event id>&limit=100` returns a page of events, oldest first,
  redacted like the stream. The Claude Code worker is unchanged (it reads `context_snapshot.folders` and
  `prompt_snapshot.system_prompt` / `claude_code_tools` as today).
- **admin-ui:** `ClaudeCodeSessionForm` becomes the Generate documents form (`NewGeneration` wraps it for the route);
  `ClaudeCodeSessionsView` is the **Generate documents** list; `ClaudeCodeSessionView` is the session page at
  `/documentation/generations/:sessionId` with fixed-height panels, follow/jump and history paging. The one-shot pages,
  components, hooks and library functions are deleted; the sidebar loses **Sessions**; the agent Test tab links to
  `/documentation/generations/new?agent=<id>`.

**Tech Stack:**
- PostgreSQL 17 with pgvector, golang-migrate SQL migrations, `db/tests/run.sh`;
- FastAPI, pydantic v2, psycopg3 (`services/admin-api`), pytest against a disposable PostgreSQL container
  (`knowledge-agent-v3-postgres:17.10-vector0.8.6`);
- React Router 7 with shadcn/ui, Vitest with Testing Library (`services/admin-ui`).

**Spec:** `docs/superpowers/specs/2026-10-07-knowledge-agents-design.md`, section **Documentation generation** and
**Delivery steps → 4. Documentation is generated only with an agent** (binding). Native agents in the form arrive with
step 3; server access through the agent's SSH configuration arrives with step 2.

## Global Constraints

- Implementers and reviewers never run make targets and never touch .env or .local; compose checks use a temp .env.
- Credentials never logged, stored in events or passed to Bash. The context snapshot and the first message hold names,
  addresses, descriptions and container paths only: never SSH keys, credential references or model keys.
- Commit trailer: `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Python stays compatible with 3.12.
- Migration `0073_remove_documentation_generation` (`.up.sql` and `.down.sql`); `db/tests/run.sh` rollback pin
  `71` → `72`. Both files are generated by the exact commands in Task 2 (they copy function and table definitions from
  0040, 0055 and 0058 by line number) and are committed as generated.
- `documents.phase_metadata jsonb` with `CONSTRAINT documents_phase_metadata_object CHECK (phase_metadata IS NULL OR
  jsonb_typeof(phase_metadata) = 'object')`. `export_input` returns `{**phase_metadata, 'source_commits': commits}` when
  it is set, else `None` (same shape as before).
- Session input (`SessionInput`, `extra='forbid'`): `agent_id` (1–100 chars), `code_project_ids` (≤ 20),
  `environment_ids` (≤ 20), `service_ids` (≤ 50), `server_ids` (≤ 50), each deduplicated keeping the first,
  `instructions` (≤ 20000, no null character, stripped), `document_name` (≤ 240, letters or digits), `folder` (the
  documentation folder rule). Texts:
  - `NOTHING_CHOSEN = 'Choose at least one code project, environment, service or server to document.'` (validation
    error, 400);
  - `NO_FOLDER = 'Claude Code needs at least one working folder: choose code projects, or environments or services with
    code projects, or give the agent code projects.'` (400);
  - `AGENT_MODEL = 'The agent has no enabled Claude model. Choose one on its General tab.'` (400, also for a disabled
    or non-Claude agent model);
  - unknown ids: `Select available code projects.`, `Select available environments.`, `Select available services.`,
    `Select available servers.` (400); a chosen code project without folders: `The code project <name> has no
    folders.` (400);
  - a missing folder: `The folder <path> of the code project <name> does not exist. Check the code project's folders.`
    (chosen or linked) and, unchanged, `The folder <path> of the agent's code project <name> does not exist. Remove the
    project from the agent or check its folders.` (400).
- Working folders, in order, duplicates removed keeping the first: chosen code projects (chosen order, their folders
  sorted); the code projects linked to each chosen environment's services (environment order, projects by name);
  the code projects linked to each chosen service (service order, projects by name); the agent's own code projects
  (Code projects switch on). Archived code projects are never linked. Each folder entry is
  `{'path', 'kind': 'code_project', 'name', 'code_project_id'}`.
- Context snapshot: `{'code_projects', 'environments', 'services', 'servers', 'instructions', 'folders',
  'code_project_ids'}`; `code_project_ids` are the chosen code projects only (they link the saved document, as today).
  Prompt snapshot: `{'system_prompt': agent_system_prompt(config), 'claude_code_tools', 'agent_version'}`.
- First message (`generate_documents.first_message`): sections joined by a blank line, empty sections left out:
  `Document the following.`; `## Code` with `- <name> — <path>, <path>`; `## Environments` with
  `- <name>[: <description>] — servers: <name> (<host>), …; services: <name>, …; deployments: <service> on <server>, …;
  code: <project> (<path>, …), …`; `## Services` with `- <name>[: <description>] — deployed on <server>
  (<environment>), …; code: <project> (<path>, …), …`; `## Servers` with `- <name> (<host>)[: <description>]`;
  `## Instructions` with the text; then `claude_code.FINAL_ANSWER` (unchanged) as the last paragraph.
- Event history: `GET /api/claude-code/sessions/{id}/events/history`, `before` optional (`ge=1`), `limit` default
  100 (`1..500`); response `{'events': [...oldest first...], 'has_more': bool}`; each event is `stream_event(row)` (a
  tool result has no `output`); `Cache-Control: no-store`; 404 `Session not found.` for another project's or an unknown
  session.
- UI texts: kind checkboxes **Code**, **Environment**, **Services**, **Servers**; pickers **Code projects**,
  **Environments**, **Services**, **Servers**; agent help `The agent works with its own model, prompt, instructions,
  skills and tools (<model name>).`; `This agent has no model. Choose a Claude model on its General tab.`; list button
  **New**; session page back link **Generate documents**; Activity: `Beginning of the session`, `Loading earlier
  activity…`, **Jump to latest**.
- Removed: `knowledge_admin.documentation_generation`, `.documentation_workflows`, `.documentation_updates`,
  `.api_evidence`, `claude_code.first_message`, `save_document(generation_id=…)`, `PROMPT_CHANGED`, the
  `knowledge-doc-worker` script, the `documentation-worker` service; UI `DocumentationWorkflowView`,
  `DocumentationGenerationForm`, `DocumentationGenerationsView`, `CodeProjectDocumentationForm`,
  `CodeDocumentationPhase`, `CodeGraphPhase`, `ApiAnalysisProgress`, `DocumentationTokenUsage`,
  `useDocumentationDraft`, routes `documentationWorkflow.tsx` and `documentationWorkflowNew.tsx`.
- Kept: **Docs → Prompts** and its tables (a prompt library; nothing generates with it any more), code graphs and
  `code_index` jobs, `document_versions.origin = 'generated'` on old versions.

## Review Focus

1. **The form overrides the agent or grants more access than it describes.** Examples: `model_id`, `prompt_id`,
   `comments`, `folders` or `notes` still accepted; a session runs with a model other than the agent's; a server's
   credential reference, SSH key or a model key in the snapshot or the message; folders of code projects that were
   neither chosen, linked to a chosen environment or service, nor the agent's own; an archived linked project's folder.
   Pinned in:
   - Task 3: `test_generate_documents_sends_one_message_and_opens_the_folders`;
   - Task 3: `test_session_input_is_validated` (every removed field → 400);
   - Task 3: `test_session_uses_the_agent_prompt_instructions_skills_tools_and_code_projects`.
2. **Removing the generation loses or moves saved documents.** Examples: a document written by a service phase
   re-exports into `<slug>/README.md` because its job is gone; document versions deleted with their run; the document
   page fails on a dropped table; the export backfill rewrites files. Pinned in:
   - Task 1: `test_the_one_shot_generation_is_gone` (document detail still works without `workflows`);
   - Task 2: `test_up_removes_the_generation_its_history_and_runs_but_keeps_documents` (phase layout kept).
3. **The migration fails on real data or breaks rollback and new workspaces.** Examples: deleting job history trips
   `guard_job_update` or `reject_mutation`; `seed_api_documentation_prompt` still updates the dropped
   `documentation_workflows`, so creating a workspace fails; 0073 down cannot restore what 0055 and 0040 downs drop.
   Pinned in:
   - Task 2: `test_new_workspaces_still_get_the_api_documentation_prompt`;
   - Task 2: `test_down_restores_the_empty_schema_and_keeps_documents`;
   - Task 2: `bash db/tests/run.sh` and `test_document_migrations_roundtrip_and_preserve_populated_tables` (every down
     from the latest to 0039).
4. **History and stream disagree.** Examples: events shown twice or skipped between the latest page and the stream;
   history returns the full tool output or another session's events; loading older events jumps the scroll position;
   Activity does not follow, or keeps following after the reader scrolled up. Pinned in:
   - Task 4: `test_event_history_pages_backwards_like_the_stream`;
   - Task 7: `opening shows the latest page, streams after its newest event and loads earlier events near the top`.
5. **Leftovers of the old navigation.** Examples: the **Sessions** menu item; links to `/documentation/sessions`,
   `?scope=agent` or workflow pages; the document page's **View phases / regenerate**; a route that still renders a
   workflow. Pinned in:
   - Task 6: `Docs → Generate documents lists generations with status, agent, tokens and document`;
   - Task 6: `old session links open the generation` (redirects);
   - Task 6: `adminLayout.test.tsx` and `agentTypes.test.tsx` expectations;
   - Task 8: the leftover `git grep` checks.

## Decisions (where the spec leaves a choice)

- **`FINAL_ANSWER` stays as the last paragraph of the first message.** The draft is the agent's final answer of each
  turn; without the sentence an agent ends with a summary instead of the document. It is plain text a user could have
  typed, so the message stays "exactly what a user could have typed"; everything else follows the spec's example.
- **Linked code project paths are shown in the message** (`code: Payments API (/work/data/code/app)`), because they
  are working folders the agent should open; the spec's example abbreviates them.
- **Without any working folder the session is refused (`NO_FOLDER`).** The Claude Code CLI needs a working directory
  (`cwd = folders[0]`). A servers-only request works when the agent has code projects; otherwise the form shows the
  server's message. Native agents (step 3) will not need a folder.
- **Phase-exported documents keep their export folder** through `documents.phase_metadata`, copied by the migration
  from the latest generation job of each document. Versions keep `origin = 'generated'` and their `generation_id`
  (without a foreign key) as history.
- **The down migration restores the schema, not the history**, so earlier migrations can roll back. The restored
  `document_versions` foreign key is `NOT VALID` because old versions still reference removed runs.
- **Files the generation wrote for runs:** its agents worked in temporary directories under `AGENT_WORKSPACE_ROOT`
  that were removed after each run, and its only lasting files are the document exports, which stay with their
  documents. Its run history lived in the database (jobs, job events, run records) and goes with the migration.
- **Audit events stay.** `ka.audit_events` is an append-only log (`reject_mutation`); entries about removed workflows
  and generations remain as history. Run records (`doc:*`) go: they are the generation's own history and the Agent runs
  page would link to removed runs.
- **Docs → Prompts stays** as a library of documentation prompts to copy into a documentation agent's prompt; its
  tables and seeds are untouched except the seed function's workflow update.
- **Component file names stay** (`ClaudeCodeSession*`); step 3 generalises sessions and renames them with the
  `/api/agent-sessions` move. Only the routes, texts and behaviour change here.
- **Session snapshots of earlier sessions keep their old keys** (`servers`, `notes`, prompt fields); nothing reads them
  except the worker's `folders`, `system_prompt` and `claude_code_tools`, which both shapes have.
- **Agent runs → Run type** offers `claude_code` instead of `documentation` (only the removed generation wrote
  `documentation` runs).

## Delivery and test boundaries

Tasks land in order on `feature/generate-documents`, and every suite is green after each task:
- Task 1 removes the generation code, routes, worker and their tests while the tables still exist.
- Task 2 (migration) needs Task 1: no code reads the dropped tables any more.
- Task 3 (session request and first message) and Task 4 (event history) are independent of each other and need
  Task 1 (constants moved out of the removed module).
- Task 5 (the form) needs Task 3's request shape. Task 6 (routes, list, navigation, one-shot UI removal) needs Task 5.
  Task 7 (session page) needs Task 4 and Task 6.
- Task 8 updates the docs and runs every suite.

---

### Task 1: admin-api — remove the one-shot generation code, routes and worker

**Files:**
- Delete: `services/admin-api/src/knowledge_admin/documentation_generation.py`
- Delete: `services/admin-api/src/knowledge_admin/documentation_workflows.py`
- Delete: `services/admin-api/src/knowledge_admin/documentation_updates.py`
- Delete: `services/admin-api/src/knowledge_admin/api_evidence.py`
- Modify: `services/admin-api/src/knowledge_admin/api.py`
- Modify: `services/admin-api/src/knowledge_admin/documents.py` (`SAME_DOCUMENT_NAME`, routes, `get_document`,
  `save_document`)
- Modify: `services/admin-api/src/knowledge_admin/repository_api.py` (`process_project`)
- Modify: `services/admin-api/src/knowledge_admin/claude_code_api.py` (imports, `PROMPT_CHANGED`)
- Modify: `services/admin-api/pyproject.toml`
- Modify: `deploy/compose.yaml`
- Modify: `tests/test_setup.py`
- Create: `services/admin-api/tests/test_generate_documents.py`
- Delete: `services/admin-api/tests/test_documentation_generation.py`, `test_documentation_workflows.py`,
  `test_api_evidence.py`, `test_code_documentation_phases.py`, `test_project_documentation.py`,
  `test_documentation_updates.py`
- Modify: `services/admin-api/tests/test_code_processing.py`, `test_run_record_callers.py`, `test_documents.py`,
  `test_claude_code_sessions.py`

**Interfaces:**
- Produces: `knowledge_admin.documents.SAME_DOCUMENT_NAME` (moved, same text);
  `knowledge_admin.claude_code_api.PROMPT_CHANGED` (moved, same text; removed in Task 3);
  `save_document(conn, project, actor, value, document_id=None, folder=None)` (no `generation_id`; versions are
  `manual`); `POST /api/code-projects/{id}/process` without `workflow_id`.

- [ ] **Step 1: Write the failing test**

Create `services/admin-api/tests/test_generate_documents.py`:

```python
"""Docs → Generate documents is the only way to generate: the one-shot generation and its worker are gone."""
import importlib.util
import inspect
import tomllib
from pathlib import Path
from uuid import uuid4

from knowledge_admin.documents import save_document
from test_api import _client
from test_documents import VALUE
from test_repositories import HEADERS

ROOT = Path(__file__).resolve().parents[3]


def test_the_one_shot_generation_is_gone(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    for method, path in (('GET', '/api/documentation/generations'), ('POST', '/api/documentation/generations'),
                         ('GET', f'/api/documentation/generations/{uuid4()}/api-analysis'),
                         ('GET', '/api/documentation/workflows'), ('POST', '/api/documentation/workflows'),
                         ('GET', f'/api/documentation/workflows/{uuid4()}')):
        assert client.request(method, path, headers=HEADERS, json={}).status_code == 404, (method, path)
    for module in ('documentation_generation', 'documentation_workflows', 'documentation_updates', 'api_evidence'):
        assert importlib.util.find_spec(f'knowledge_admin.{module}') is None, module
    assert 'generation_id' not in inspect.signature(save_document).parameters
    scripts = tomllib.loads((ROOT / 'services/admin-api/pyproject.toml').read_text())['project']['scripts']
    assert 'knowledge-doc-worker' not in scripts
    # Saved documents stay: the document page no longer links generation workflows.
    created = client.post('/api/documentation/documents', headers=HEADERS, json=VALUE)
    assert created.status_code == 201, created.text
    detail = client.get(f"/api/documentation/documents/{created.json()['id']}", headers=HEADERS)
    assert detail.status_code == 200 and detail.json()['title'] == 'Payments runbook'
    assert 'workflows' not in detail.json()
```

In `tests/test_setup.py`, in `test_compose_mounts_the_data_root_read_only_and_writer_folders_writable`, remove the
`"documentation-worker": ["index", "documents"], ` entry from `writers` and change the retired loop to
`for retired in ("docs-wiki", "web", "oauth2-proxy", "documentation-worker"):`.

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_generate_documents.py -q`
Expected: FAIL: `GET /api/documentation/generations` answers 200, not 404.

- [ ] **Step 3: Implement**

Delete the four modules and the six test files:

```bash
git rm services/admin-api/src/knowledge_admin/documentation_generation.py \
  services/admin-api/src/knowledge_admin/documentation_workflows.py \
  services/admin-api/src/knowledge_admin/documentation_updates.py \
  services/admin-api/src/knowledge_admin/api_evidence.py \
  services/admin-api/tests/test_documentation_generation.py services/admin-api/tests/test_documentation_workflows.py \
  services/admin-api/tests/test_api_evidence.py services/admin-api/tests/test_code_documentation_phases.py \
  services/admin-api/tests/test_project_documentation.py services/admin-api/tests/test_documentation_updates.py
```

`services/admin-api/src/knowledge_admin/api.py`: the import line becomes

```python
from knowledge_admin import documents, documentation_prompts, documentation_folders, environments, agent_sessions, agent_versions, board, claude_code_api, jira_api, repository_api, service_api, rag_api, skills, storage
```

and remove `router.include_router(documentation_workflows.build_api(app_name, repository_store))` and
`router.include_router(documentation_generation.build_api(app_name, repository_store))`.

`services/admin-api/src/knowledge_admin/documents.py`:
- under `GitCommit = …`, add

```python
SAME_DOCUMENT_NAME = 'A document with this name already exists in this folder. Confirm to save a new version.'
```

- `save_document`: the signature becomes
  `def save_document(conn, project: UUID, actor: UUID | None, value: DocumentInput, document_id: UUID | None = None,
  folder: str | None = None) -> dict:`; in its version insert, replace the column list end `origin,created_by,generation_id)`
  with `origin,created_by)`, drop one `%s` from `VALUES`, and replace the last three values
  `'generated' if generation_id is not None else 'manual',actor,generation_id` with `'manual',actor`;
- `get_document`: delete the `row['workflows'] = conn.execute(...)` statement (three lines);
- delete the two routes `@router.get('/generations/{job_id}/api-analysis')` (`api_analysis`) and
  `@router.get('/generations')` (`generations`) at the end of `build_api`, keeping `return router`.

`services/admin-api/src/knowledge_admin/repository_api.py`, `process_project`: the signature becomes
`def process_project(repository_id: UUID, request: Request):`; delete the `if workflow_id is not None and …` check
(three lines with its `raise`) and the final `if workflow_id is not None:` block (three lines).

`services/admin-api/src/knowledge_admin/claude_code_api.py`: replace
`from knowledge_admin.documentation_generation import PROMPT_CHANGED, SAME_DOCUMENT_NAME` with nothing, add
`SAME_DOCUMENT_NAME` to the existing `from knowledge_admin.documents import …` line, and add under `NOTHING_TO_STOP`:

```python
PROMPT_CHANGED = 'The prompt changed. Reload the prompt before generating.'  # removed with the prompt field (Task 3)
```

`services/admin-api/pyproject.toml`: delete the line
`knowledge-doc-worker = "knowledge_admin.documentation_generation:main"`.

`deploy/compose.yaml`: delete the `documentation-worker:` service: lines 185–214, from `  documentation-worker:` to the
blank line before `  claude-code-worker:`. Check: `grep -n "documentation-worker\|knowledge-doc-worker" deploy/compose.yaml`
prints nothing.

Tests:
- `services/admin-api/tests/test_code_processing.py`: delete `test_missing_code_skips_models_and_saves_clear_document`,
  `test_two_diagrams_keep_code_shared_and_deployment_scoped` and
  `test_a_queued_generation_snapshot_is_rebased_before_it_runs`;
- `services/admin-api/tests/test_run_record_callers.py`: delete `test_documentation_runs_get_unique_records_per_job`;
- `services/admin-api/tests/test_documents.py`: in `test_prompt_drafts_activation_disable_and_delete` delete the last
  line (`assert client.get('/api/documentation/generations',…) == {'generations':[]}`); in the parameters of
  `test_documentation_requires_admin` remove `('GET','/documentation/generations'),`;
- `services/admin-api/tests/test_claude_code_sessions.py`: replace
  `from knowledge_admin.documentation_generation import PROMPT_CHANGED, SAME_DOCUMENT_NAME` with
  `from knowledge_admin.claude_code_api import PROMPT_CHANGED` and `from knowledge_admin.documents import SAME_DOCUMENT_NAME`.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_generate_documents.py tests/test_documents.py tests/test_code_processing.py tests/test_run_record_callers.py tests/test_claude_code_sessions.py tests/test_documentation_fixture.py tests/test_environments.py -q`
Expected: PASS.

Run: `cd services/admin-api && git grep -n "documentation_generation\|documentation_workflows\|documentation_updates\|api_evidence\|generation_id=" -- src tests`
Expected: no matches.

Run: `python3 -m unittest discover -s tests -p test_setup.py`
Expected: OK (the compose test uses `docker compose config` with a temporary `.env`).

- [ ] **Step 5: Commit**

```bash
git add -A services/admin-api/src services/admin-api/tests services/admin-api/pyproject.toml deploy/compose.yaml tests/test_setup.py
git commit -m "refactor(admin-api): remove the one-shot documentation generation, its workflows and worker

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Migration 0073 — drop the generation's tables and history; documents keep their export layout

**Files:**
- Create: `db/migrations/0073_remove_documentation_generation.up.sql`
- Create: `db/migrations/0073_remove_documentation_generation.down.sql`
- Modify: `db/tests/run.sh` (rollback pin `71` → `72`)
- Modify: `db/tests/documentation.sql`
- Modify: `services/admin-api/src/knowledge_admin/documents.py` (`export_input`)
- Create: `services/admin-api/tests/test_generate_documents_migration.py`
- Modify: `services/admin-api/tests/test_documentation_fixture.py`
- Modify: `services/admin-api/tests/test_environments.py`

**Interfaces:**
- Consumes: Task 1 (no code reads the generation tables).
- Produces: `ka.documents.phase_metadata`; `documents.export_input` reads it; `jobs.kind` no longer accepts
  `documentation_generate`.

- [ ] **Step 1: Write the failing tests**

Create `services/admin-api/tests/test_generate_documents_migration.py`:

```python
"""0073: the one-shot generation goes with its tables and history; documents and their export layout stay."""
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from knowledge_admin.documents import export_input

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0073_remove_documentation_generation.up.sql', '0073_remove_documentation_generation.down.sql'
GONE = ('documentation_generations', 'documentation_workflows', 'documentation_workflow_jobs',
        'documentation_workflow_details')


def run(conn, name: str):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def count(conn, sql: str, *params) -> int:
    return conn.execute(f'SELECT count(*) AS n FROM {sql}', params).fetchone()['n']


def generated_document(conn, project) -> dict:
    """What the one-shot generation left behind (before 0073): a succeeded job with its phase layout, its events and
    run record, a workflow, and a document version it wrote."""
    job, document, version = uuid4(), uuid4(), uuid4()
    conn.execute("INSERT INTO ka.jobs(id,project_id,kind) VALUES (%s,%s,'documentation_generate')", (job, project))
    conn.execute("""INSERT INTO ka.documentation_generations(job_id,project_id,mode,instructions,agent_snapshot,
        prompt_snapshot,source_snapshot) VALUES (%s,%s,'create','Write','{}','[]','{}')""", (job, project))
    conn.execute('INSERT INTO ka.documents(id,project_id,slug,current_version_id) VALUES (%s,%s,%s,%s)',
                 (document, project, f'payments-api-{document.hex[:6]}', version))
    conn.execute("""INSERT INTO ka.document_versions(id,document_id,project_id,version,title,description,purpose,audience,
        markdown,content_hash,source_snapshot,origin,generation_id) VALUES (%s,%s,%s,1,'Payments API','','api','both',
        '# API',encode(sha256(convert_to('# API','UTF8')),'hex'),%s,'generated',%s)""",
        (version, document, project, Jsonb({'code_projects': []}), job))
    metadata = {'phase': 'api', 'service_id': str(uuid4()), 'service_name': 'Payments', 'job_id': str(job)}
    conn.execute("UPDATE ka.jobs SET status='succeeded',started_at=now(),finished_at=now(),result=%s WHERE id=%s",
                 (Jsonb({'documents': [], 'phase_metadata': metadata}), job))
    conn.execute("INSERT INTO ka.job_events(job_id,event_type,message) VALUES (%s,'succeeded','Documents saved')", (job,))
    conn.execute("""INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,payload)
        VALUES (%s,%s,1,'start','search','{}')""", (project, f'doc:{job}:abc'))
    workflow = uuid4()
    conn.execute("""INSERT INTO ka.documentation_workflows(id,project_id,name,scope)
        VALUES (%s,%s,'Payments documentation','service')""", (workflow, project))
    conn.execute('INSERT INTO ka.documentation_workflow_jobs(workflow_id,job_id,project_id) VALUES (%s,%s,%s)',
                 (workflow, job, project))
    return {'job': job, 'document': document, 'version': version, 'metadata': metadata}


def test_up_removes_the_generation_its_history_and_runs_but_keeps_documents(store):
    with store.connect() as conn:
        run(conn, DOWN)
        conn.commit()
        project = store.project(conn, 'shop')
        made = generated_document(conn, project)
        conn.commit()
        run(conn, UP)
        conn.commit()
        for name in GONE:
            assert conn.execute('SELECT to_regclass(%s) AS r', (f'ka.{name}',)).fetchone()['r'] is None, name
        assert count(conn, 'ka.jobs WHERE id=%s', made['job']) == 0
        assert count(conn, 'ka.job_events WHERE job_id=%s', made['job']) == 0
        assert count(conn, "ka.agent_run_events WHERE starts_with(run_key,'doc:')") == 0
        version = conn.execute('SELECT origin,generation_id FROM ka.document_versions WHERE id=%s',
                               (made['version'],)).fetchone()
        assert (version['origin'], version['generation_id']) == ('generated', made['job'])  # history stays
        # The service phase layout moved onto the document: its export folder does not change.
        assert export_input(conn, made['document'])['phase_metadata'] == {**made['metadata'], 'source_commits': {}}
        with pytest.raises(psycopg.errors.CheckViolation):
            with conn.transaction():
                conn.execute("INSERT INTO ka.jobs(project_id,kind) VALUES (%s,'documentation_generate')", (project,))


def test_new_workspaces_still_get_the_api_documentation_prompt(store):
    with store.connect() as conn:  # the seed no longer touches the dropped workflows
        project = store.project(conn, 'fresh-after-0073')
        prompt = conn.execute('''SELECT v.version FROM ka.documentation_prompts p
            JOIN ka.documentation_prompt_versions v ON v.id=p.active_version_id
            WHERE p.id=md5('knowledge-agent-v3:documentation:api:' || %s::text)::uuid''', (project,)).fetchone()
        assert prompt['version'] == 3


def test_down_restores_the_empty_schema_and_keeps_documents(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        conn.commit()
        run(conn, DOWN)
        conn.commit()
        made = generated_document(conn, project)
        conn.commit()
        run(conn, UP)
        conn.commit()
        run(conn, DOWN)  # a version written by a removed run does not block the rollback
        conn.commit()
        for name in GONE:
            assert conn.execute('SELECT to_regclass(%s) AS r', (f'ka.{name}',)).fetchone()['r'] is not None, name
        assert count(conn, 'ka.documentation_generations') == 0
        assert count(conn, 'ka.document_versions WHERE id=%s', made['version']) == 1
        assert conn.execute("""SELECT 1 FROM information_schema.columns WHERE table_schema='ka'
            AND table_name='documents' AND column_name='phase_metadata'""").fetchone() is None
        run(conn, UP)
        conn.commit()
```

In `services/admin-api/tests/test_documentation_fixture.py`, `test_upgrade_backfills_existing_workspace_and_preserves_same_name_prompt`,
add as the first statement inside `with store.connect() as conn:`:

```python
        conn.execute((MIGRATIONS / '0073_remove_documentation_generation.down.sql').read_text(), prepare=False)
```

In `services/admin-api/tests/test_environments.py`, `test_migration_reapply_and_populated_rollback_protection`, add
before the `0050_service_environments.down.sql` statement:

```python
        conn.execute((migrations / "0073_remove_documentation_generation.down.sql").read_text(), prepare=False)
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_generate_documents_migration.py -q`
Expected: FAIL with `FileNotFoundError` for `0073_remove_documentation_generation.down.sql`.

- [ ] **Step 3: Implement**

Generate the up migration (from the repository root; it copies `seed_api_documentation_prompt` from 0058 without the
six lines 38–43 that update workflow drafts):

```bash
{
cat <<'SQL'
BEGIN;
SET search_path = ka, public;

-- Docs → Generate documents runs agent sessions only. The one-shot generation goes with its saved workflows and
-- history (jobs, job events, run records). Saved documents, their versions and exports stay.

-- A document written by a service phase keeps exporting into its phase folder: the layout used to be read from the
-- generation job, so it moves onto the document.
ALTER TABLE documents ADD COLUMN phase_metadata jsonb
    CONSTRAINT documents_phase_metadata_object CHECK (phase_metadata IS NULL OR jsonb_typeof(phase_metadata) = 'object');
ALTER TABLE documents DISABLE TRIGGER touch_updated_at;
UPDATE documents d SET phase_metadata = latest.metadata FROM (
    SELECT DISTINCT ON (v.document_id) v.document_id, coalesce(
        nullif(nullif(j.result->'phase_metadata', 'null'::jsonb), '{}'::jsonb),
        nullif(nullif(j.result->'overview_metadata', 'null'::jsonb), '{}'::jsonb)) AS metadata
    FROM document_versions v JOIN jobs j ON j.id = v.generation_id
    ORDER BY v.document_id, v.version DESC) latest
WHERE latest.document_id = d.id AND jsonb_typeof(latest.metadata) = 'object';
ALTER TABLE documents ENABLE TRIGGER touch_updated_at;

-- Versions keep their origin and the id of the run that wrote them; the run itself is gone.
ALTER TABLE document_versions DROP CONSTRAINT document_versions_generation_id_project_id_fkey;
COMMENT ON COLUMN document_versions.generation_id IS 'The removed one-shot generation run that wrote this version (history only since 0073).';

DROP VIEW documentation_workflow_details;
DROP TABLE documentation_workflow_jobs, documentation_workflows, documentation_generations;
DROP FUNCTION check_documentation_job();

-- Its jobs with their events and questions, and the run records of its agent steps (run keys doc:<job>:<step>).
ALTER TABLE job_events DISABLE TRIGGER reject_mutation;
DELETE FROM job_events e USING jobs j WHERE e.job_id = j.id AND j.kind = 'documentation_generate';
ALTER TABLE job_events ENABLE TRIGGER reject_mutation;
DELETE FROM job_questions q USING jobs j WHERE q.job_id = j.id AND j.kind = 'documentation_generate';
ALTER TABLE jobs DISABLE TRIGGER guard_job_update;
DELETE FROM jobs WHERE kind = 'documentation_generate';
ALTER TABLE jobs ENABLE TRIGGER guard_job_update;
DELETE FROM agent_run_events WHERE starts_with(run_key, 'doc:');

ALTER TABLE jobs DROP CONSTRAINT jobs_kind_check, DROP CONSTRAINT jobs_check, DROP CONSTRAINT jobs_check1;
ALTER TABLE jobs ADD CONSTRAINT jobs_kind_check CHECK (kind IN
    ('repository_sync','code_index','jira_sync','server_test','rag_folder_index','rag_collection_sync'));
ALTER TABLE jobs ADD CONSTRAINT jobs_check CHECK (
    num_nonnulls(repository_id, jira_connection_id, server_id, rag_folder_id, rag_collection_id) = 1);
ALTER TABLE jobs ADD CONSTRAINT jobs_check1 CHECK (
    (kind IN ('repository_sync','code_index') AND repository_id IS NOT NULL)
    OR (kind = 'jira_sync' AND jira_connection_id IS NOT NULL)
    OR (kind = 'server_test' AND server_id IS NOT NULL)
    OR (kind = 'rag_folder_index' AND rag_folder_id IS NOT NULL)
    OR (kind = 'rag_collection_sync' AND rag_collection_id IS NOT NULL));

-- 0058's API prompt seed also refreshed unchanged workflow drafts; without workflows it seeds the prompt only.
SQL
sed -n '4,37p;44,46p' db/migrations/0058_graph_directed_api_documentation.up.sql | sed '1s/^CREATE FUNCTION/CREATE OR REPLACE FUNCTION/'
echo 'COMMIT;'
} > db/migrations/0073_remove_documentation_generation.up.sql
```

Generate the down migration (it copies the table, function and triggers from 0040 lines 60–84, the workflow tables,
indexes and view from 0055 lines 3–29 and 81–94, and the full 0058 seed function, lines 4–46):

```bash
{
cat <<'SQL'
BEGIN;
SET search_path = ka, public;

-- 0073 removed the one-shot generation with its history, which does not come back. This restores its empty schema so
-- that earlier migrations can roll back. Versions written by a removed run keep their generation_id, so the restored
-- foreign key leaves existing rows unchecked.
ALTER TABLE documents DROP COLUMN phase_metadata;
ALTER TABLE jobs DROP CONSTRAINT jobs_kind_check, DROP CONSTRAINT jobs_check, DROP CONSTRAINT jobs_check1;
ALTER TABLE jobs ADD CONSTRAINT jobs_kind_check CHECK (kind IN
    ('repository_sync','code_index','documentation_generate','jira_sync','server_test','rag_folder_index','rag_collection_sync'));
ALTER TABLE jobs ADD CONSTRAINT jobs_check CHECK (
    num_nonnulls(repository_id, jira_connection_id, server_id, rag_folder_id, rag_collection_id) =
    CASE WHEN kind = 'documentation_generate' THEN 0 ELSE 1 END);
ALTER TABLE jobs ADD CONSTRAINT jobs_check1 CHECK (
    kind = 'documentation_generate' OR (kind IN ('repository_sync','code_index') AND repository_id IS NOT NULL)
    OR (kind = 'jira_sync' AND jira_connection_id IS NOT NULL)
    OR (kind = 'server_test' AND server_id IS NOT NULL)
    OR (kind = 'rag_folder_index' AND rag_folder_id IS NOT NULL)
    OR (kind = 'rag_collection_sync' AND rag_collection_id IS NOT NULL));

SQL
sed -n '60,84p' db/migrations/0040_documentation.up.sql
cat <<'SQL'
ALTER TABLE document_versions ADD CONSTRAINT document_versions_generation_id_project_id_fkey
    FOREIGN KEY (generation_id, project_id) REFERENCES documentation_generations(job_id, project_id) NOT VALID;
COMMENT ON COLUMN document_versions.generation_id IS NULL;

SQL
sed -n '3,29p;81,94p' db/migrations/0055_documentation_sessions.up.sql
echo
sed -n '4,46p' db/migrations/0058_graph_directed_api_documentation.up.sql | sed '1s/^CREATE FUNCTION/CREATE OR REPLACE FUNCTION/'
echo 'COMMIT;'
} > db/migrations/0073_remove_documentation_generation.down.sql
```

Check the generated files:
- `grep -n "documentation_workflows" db/migrations/0073_remove_documentation_generation.up.sql` prints only the
  `DROP TABLE` line (26);
- `grep -c "CREATE OR REPLACE FUNCTION seed_api_documentation_prompt" db/migrations/0073_*.sql` prints `1` for each
  file, and `grep -c "UPDATE documentation_workflows" db/migrations/0073_*.sql` prints `0` (up) and `1` (down);
- `wc -l db/migrations/0073_*` prints 89 (up) and 135 (down).

`db/tests/run.sh`: change `[ "$rolled_back_version" = 71 ]` to `[ "$rolled_back_version" = 72 ]`.

`db/tests/documentation.sql`: replace the last three statements before `ROLLBACK;` (four lines: the
`INSERT INTO jobs … \gset`, the `INSERT INTO documentation_generations …` with its `VALUES` line and the `generation
jobs support multiple document outputs…` assertion) with:

```sql
SELECT pg_temp.assert_true(to_regclass('ka.documentation_generations') IS NULL
    AND to_regclass('ka.documentation_workflows') IS NULL, 'the one-shot documentation generation is retired');
SELECT pg_temp.assert_true((SELECT pg_get_constraintdef(oid) NOT LIKE '%documentation_generate%' FROM pg_constraint
    WHERE conname='jobs_kind_check' AND conrelid='ka.jobs'::regclass), 'jobs no longer accept documentation generation');
```

`services/admin-api/src/knowledge_admin/documents.py`, replace `export_input` with:

```python
def export_input(conn, document_id: UUID) -> dict | None:
    """Export input for a document's current version: slug, markdown, revision, source commits and phase metadata (a
    document written by a service phase keeps exporting into its phase folder)."""
    row = conn.execute('''SELECT d.slug, d.folder, d.revision, d.phase_metadata, v.markdown, v.source_snapshot
        FROM ka.documents d JOIN ka.document_versions v ON v.id=d.current_version_id WHERE d.id=%s''', (document_id,)).fetchone()
    if row is None:
        return None
    commits = {item['id']: item['git_commit'] for item in row['source_snapshot']['code_projects'] if item.get('git_commit')}
    phase_metadata = {**row['phase_metadata'], 'source_commits': commits} if row['phase_metadata'] else None
    return {'slug': row['slug'], 'folder': row['folder'], 'markdown': row['markdown'], 'revision': row['revision'],
            'source_commits': commits, 'phase_metadata': phase_metadata}
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_generate_documents_migration.py tests/test_generate_documents.py tests/test_documents.py tests/test_documentation_fixture.py tests/test_environments.py tests/test_documentation_folders.py tests/test_document_folders_migration.py tests/test_knowledge_agents_migration.py -q`
Expected: PASS (`test_document_migrations_roundtrip_and_preserve_populated_tables` runs every down from 0073 to 0039
and every up again).

Run: `bash db/tests/run.sh`
Expected: every `PASS:` line, including `the one-shot documentation generation is retired`, and exit code 0.

- [ ] **Step 5: Commit**

```bash
git add db/migrations/0073_remove_documentation_generation.up.sql db/migrations/0073_remove_documentation_generation.down.sql \
  db/tests/run.sh db/tests/documentation.sql services/admin-api/src/knowledge_admin/documents.py \
  services/admin-api/tests/test_generate_documents_migration.py services/admin-api/tests/test_documentation_fixture.py \
  services/admin-api/tests/test_environments.py
git commit -m "feat(db): drop the one-shot generation's tables and history; documents keep their export layout (0073)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: The Generate documents request — what to document becomes one message and the working folders

**Files:**
- Create: `services/admin-api/src/knowledge_admin/generate_documents.py`
- Modify: `services/admin-api/src/knowledge_admin/claude_code_api.py` (`SessionInput`, `session_context`,
  `create_session`)
- Modify: `services/admin-api/src/knowledge_admin/claude_code.py` (remove `first_message`)
- Modify: `services/admin-api/tests/test_claude_code_sessions.py`

**Interfaces:**
- Consumes: Task 1's `documents.SAME_DOCUMENT_NAME`; step 1's `get_agent_config`, `agent_system_prompt`.
- Produces: `generate_documents.subjects(conn, project, value) -> dict`,
  `generate_documents.first_message(context) -> str`; `claude_code_api.NOTHING_CHOSEN`, `NO_FOLDER`, `AGENT_MODEL`;
  `POST /api/claude-code/sessions` body `{agent_id, code_project_ids, environment_ids, service_ids, server_ids,
  instructions, document_name, folder}`.

- [ ] **Step 1: Write the failing tests**

In `services/admin-api/tests/test_claude_code_sessions.py`:

Replace the imports from `knowledge_admin` with:

```python
from knowledge_admin import claude_code_api
from knowledge_admin.claude_code import FINAL_ANSWER, IDLE_CLOSED, SYSTEM_PROMPT
from knowledge_admin.claude_code_api import AGENT_MODEL, NO_FOLDER, NOTHING_CHOSEN
from knowledge_admin.documents import SAME_DOCUMENT_NAME
```

Replace the `sessions` fixture and `start` with:

```python
@pytest.fixture
def sessions(store, tmp_path, monkeypatch):
    """A Claude Code agent with a Claude model; code projects Payments API (app) and Payments worker (worker); the
    Production environment with the Payments service (both projects, deployed on prod-1) and the prod-1 server."""
    data = tmp_path / 'data'
    for folder in ('code/app', 'code/worker', 'jira', 'generated-documents'):
        (data / folder).mkdir(parents=True)
    monkeypatch.setenv('DATA_ROOT_DIR', str(data))
    monkeypatch.setenv('CODE_ROOT', str(data / 'code'))
    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', str(data / 'generated-documents'))
    client = _client(tmp_path, repository_store=store)
    claude = client.post('/api/llm/models', headers=HEADERS, json={
        'name': 'Claude', 'provider': 'bedrock', 'model': 'eu.anthropic.claude-sonnet-4-5-20250929-v1:0',
        'api_format': 'native', 'aws_region': 'eu-central-1', 'api_key': 'bedrock-secret-token'}).json()['id']
    gpt = client.post('/api/llm/models', headers=HEADERS, json={
        'name': 'GPT', 'provider': 'openai', 'model': 'gpt-5', 'api_format': 'responses',
        'base_url': 'http://model.test/v1'}).json()['id']
    agent = claude_code_agent(client, model_id=claude)
    server = client.post('/api/servers', headers=HEADERS, json={
        'name': 'prod-1', 'hostname': '10.0.0.5', 'description': 'Payments host'}).json()
    repo, worker, environment, service = uuid4(), uuid4(), uuid4(), uuid4()
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        for project_id, slug, name, folder in ((repo, 'payments-api', 'Payments API', 'app'),
                                               (worker, 'payments-worker', 'Payments worker', 'worker')):
            conn.execute('INSERT INTO ka.repositories(id,project_id,slug,name) VALUES (%s,%s,%s,%s)', (project_id, project, slug, name))
            conn.execute('INSERT INTO ka.code_project_folders(code_project_id,project_id,path) VALUES (%s,%s,%s)',
                         (project_id, project, folder))
        conn.execute("INSERT INTO ka.environments(id,project_id,name,description) VALUES (%s,%s,'Production','Live systems')",
                     (environment, project))
        conn.execute("INSERT INTO ka.services(id,project_id,name) VALUES (%s,%s,'Payments')", (service, project))
        for project_id in (repo, worker):
            conn.execute('INSERT INTO ka.service_repositories(project_id,service_id,repository_id) VALUES (%s,%s,%s)',
                         (project, service, project_id))
        conn.execute('INSERT INTO ka.environment_services(project_id,environment_id,service_id) VALUES (%s,%s,%s)',
                     (project, environment, service))
        conn.execute('INSERT INTO ka.environment_servers(project_id,environment_id,server_id) VALUES (%s,%s,%s)',
                     (project, environment, server['id']))
        conn.execute("""INSERT INTO ka.service_deployments(project_id,service_id,server_id,environment,environment_id,
            runtime_name,port) VALUES (%s,%s,%s,'Production',%s,'payments-api',8080)""", (project, service, server['id'], environment))
    body = {'agent_id': agent, 'code_project_ids': [str(repo)], 'environment_ids': [str(environment)],
            'service_ids': [str(service)], 'server_ids': [server['id']], 'instructions': ' Focus on the public API. ',
            'document_name': 'Payments – API reference', 'folder': 'payments-api-docs'}
    return SimpleNamespace(client=client, store=store, body=body, agent=agent, claude=claude, gpt=gpt, repo=str(repo),
                           worker=str(worker), environment=str(environment), service=str(service), server=server, data=data)


def start(sessions, **change) -> dict:
    response = sessions.client.post('/api/claude-code/sessions', headers=HEADERS, json={**sessions.body, **change})
    assert response.status_code == 201, response.text
    return response.json()


def refused(sessions, **change) -> str:
    response = sessions.client.post('/api/claude-code/sessions', headers=HEADERS, json={**sessions.body, **change})
    assert response.status_code == 400, response.text
    return response.json()['error']['message']
```

Replace `test_session_context_snapshot_folders_servers_and_notes` with:

```python
def test_generate_documents_sends_one_message_and_opens_the_folders(sessions):
    session = start(sessions)
    data = sessions.data
    assert (session['status'], session['turns'], session['title'], session['folder'], session['agent_name'], session['model_name']) == (
        'starting', 0, 'Payments – API reference', 'payments-api-docs', 'Technical writer', 'Claude')
    app, worker = f'{data}/code/app', f'{data}/code/worker'
    context = session['context_snapshot']
    assert context['folders'] == [  # chosen first, then linked through the environment and the service, once each
        {'path': app, 'kind': 'code_project', 'name': 'Payments API', 'code_project_id': sessions.repo},
        {'path': worker, 'kind': 'code_project', 'name': 'Payments worker', 'code_project_id': sessions.worker}]
    assert context['code_project_ids'] == [sessions.repo]  # the saved document links the chosen projects only
    assert context['servers'] == [{'id': sessions.server['id'], 'name': 'prod-1', 'hostname': '10.0.0.5',
                                   'description': 'Payments host'}]
    assert session['prompt_snapshot'] == {'system_prompt': SYSTEM_PROMPT, 'claude_code_tools': ['Read', 'Grep', 'Glob', 'Bash'],
                                          'agent_version': None}
    [first] = session['inputs']
    assert (first['seq'], first['status'], first['turn']) == (1, 'queued', None)
    assert first['text'] == '\n\n'.join([
        'Document the following.',
        f'## Code\n- Payments API — {app}',
        '## Environments\n- Production: Live systems — servers: prod-1 (10.0.0.5); services: Payments; '
        f'deployments: Payments on prod-1; code: Payments API ({app}), Payments worker ({worker})',
        f'## Services\n- Payments — deployed on prod-1 (Production); code: Payments API ({app}), Payments worker ({worker})',
        '## Servers\n- prod-1 (10.0.0.5): Payments host',
        '## Instructions\nFocus on the public API.',
        FINAL_ANSWER])
    serialized = json.dumps(session)
    for secret in ('bedrock-secret-token', 'api_key_ref', 'private_key', 'credential'):
        assert secret not in serialized
    # Servers only, without instructions: one section; the folders come from the agent's code projects (none here).
    assert refused(sessions, code_project_ids=[], environment_ids=[], service_ids=[], instructions='') == NO_FOLDER
    # A service alone opens its linked code projects.
    service_only = start(sessions, code_project_ids=[], environment_ids=[], server_ids=[], instructions='')
    assert [f['path'] for f in service_only['context_snapshot']['folders']] == [app, worker]
    assert service_only['inputs'][0]['text'] == '\n\n'.join([
        'Document the following.',
        f'## Services\n- Payments — deployed on prod-1 (Production); code: Payments API ({app}), Payments worker ({worker})',
        FINAL_ANSWER])
    # An archived project is never linked.
    with sessions.store.connect() as conn:
        conn.execute('UPDATE ka.repositories SET archived_at=now() WHERE id=%s', (sessions.worker,))
    linked = start(sessions, code_project_ids=[], environment_ids=[], server_ids=[])
    assert [f['path'] for f in linked['context_snapshot']['folders']] == [app]
```

Replace the parameters and body of `test_session_input_is_validated` with:

```python
@pytest.mark.parametrize('change, status', [
    ({'agent_id': 'search'}, 400), ({'agent_id': 'missing'}, 400),
    ({'code_project_ids': [], 'environment_ids': [], 'service_ids': [], 'server_ids': []}, 400),
    ({'code_project_ids': ['not-a-uuid']}, 400), ({'code_project_ids': [str(uuid4())]}, 400),
    ({'environment_ids': [str(uuid4())]}, 400), ({'service_ids': [str(uuid4())]}, 400), ({'server_ids': [str(uuid4())]}, 400),
    ({'folder': 'Payments'}, 400), ({'folder': 'services/x'}, 400), ({'folder': '../docs'}, 400),
    ({'document_name': '– –'}, 400), ({'instructions': 'a\x00b'}, 400), ({'instructions': 'x' * 20001}, 400),
    # The agent runs with its own model and prompt: the form cannot override them or add folders or notes.
    ({'model_id': 'CLAUDE'}, 400), ({'prompt_id': str(uuid4())}, 400), ({'prompt_version_id': str(uuid4())}, 400),
    ({'comments': 'Extra'}, 400), ({'folders': ['jira']}, 400), ({'notes': 'Deployed with Helm.'}, 400)])
def test_session_input_is_validated(sessions, change, status):
    if change.get('model_id') == 'CLAUDE':
        change = {'model_id': sessions.claude}
    response = sessions.client.post('/api/claude-code/sessions', headers=HEADERS, json={**sessions.body, **change})
    assert response.status_code == status, response.text
    if change == {'code_project_ids': [], 'environment_ids': [], 'service_ids': [], 'server_ids': []}:
        assert NOTHING_CHOSEN in response.json()['error']['message']
    with sessions.store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_sessions_cc').fetchone()['n'] == 0
```

Replace `test_a_removed_folder_or_disabled_agent_refuses_the_session` with:

```python
def test_a_removed_folder_or_disabled_agent_refuses_the_session(sessions):
    (sessions.data / 'code/app').rmdir()
    assert refused(sessions).startswith(f"The folder {sessions.data}/code/app of the code project Payments API does not exist.")
    with sessions.store.connect() as conn:
        conn.execute('UPDATE ka.agents SET enabled=false WHERE id=%s', (sessions.agent,))
    assert refused(sessions) == 'Select an enabled Claude Code agent.'
    with sessions.store.connect() as conn:  # an enabled agent with a disabled Claude model is refused as well
        conn.execute('UPDATE ka.agents SET enabled=true WHERE id=%s', (sessions.agent,))
        conn.execute('UPDATE ka.llm_models SET enabled=false WHERE id=%s', (sessions.claude,))
    assert refused(sessions) == AGENT_MODEL
    with sessions.store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_sessions_cc').fetchone()['n'] == 0
```

Replace `test_symlinked_folders_and_folders_outside_data_root_are_refused` with:

```python
def test_symlinked_code_folders_are_refused(sessions, tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    (sessions.data / 'code/app-link').symlink_to(outside)
    with sessions.store.connect() as conn:
        conn.execute("UPDATE ka.code_project_folders SET path='app-link' WHERE code_project_id=%s", (sessions.repo,))
    assert 'symbolic link' in refused(sessions)
    with sessions.store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_sessions_cc').fetchone()['n'] == 0
```

In `test_session_uses_the_agent_prompt_instructions_skills_tools_and_code_projects`:
- replace `body = {key: value for key, value in sessions.body.items() if key != 'model_id'}  # the agent's model is used`
  with `body = {**sessions.body, 'code_project_ids': [], 'environment_ids': [], 'service_ids': []}  # servers only`;
- the first request becomes `client.post('/api/claude-code/sessions', headers=HEADERS, json={**body, 'agent_id': agent})`;
- the folder assertion becomes

```python
    assert session['context_snapshot']['folders'] == [  # only the agent's own code projects
        {'path': f'{data}/code/app', 'kind': 'code_project', 'name': 'Payments API', 'code_project_id': sessions.repo}]
```

- replace `assert f'- {data}/code/app (code project Payments API)' in session['inputs'][0]['text']` with
  `assert '## Servers\n- prod-1 (10.0.0.5): Payments host' in session['inputs'][0]['text'] and '## Code' not in session['inputs'][0]['text']`;
- the "agent without a model" check becomes
  `assert missing.status_code == 400 and missing.json()['error']['message'] == AGENT_MODEL`.

In `test_session_uses_the_active_version_tools_and_code_projects`, replace
`body = {key: value for key, value in sessions.body.items() if key != 'model_id'}` with
`body = {**sessions.body, 'code_project_ids': [], 'environment_ids': [], 'service_ids': []}` and the request body with
`json={**body, 'agent_id': agent}`.

In `test_save_document_needs_confirmation_for_the_same_name_and_exports_into_the_folder`, change the comment
`# Another session with the same name and folder needs the confirmation too; a folder-only session is 'explanation'.`
to `# Another session with the same name and folder needs the confirmation too; without chosen code projects it is
'explanation'.` (the calls with `code_project_ids=[]` stay: the service's linked projects are the working folders).

Leave `test_messages_stop_and_close`, `test_idle_sessions_close_after_24_hours`, the stream tests and
`test_idle_session_is_closed_when_opened_or_sent_to` unchanged.

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_claude_code_sessions.py -q`
Expected: FAIL with `ImportError: cannot import name 'AGENT_MODEL' from 'knowledge_admin.claude_code_api'`.

- [ ] **Step 3: Implement**

Create `services/admin-api/src/knowledge_admin/generate_documents.py`:

```python
"""Docs → Generate documents: what the form asks an agent to document, as the session's context snapshot and its one
first message. Names, addresses, descriptions and container paths only: never credentials."""
import os
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder

from knowledge_admin.claude_code import FINAL_ANSWER

FOLDER_PATHS = 'ARRAY(SELECT path::text FROM ka.code_project_folders f WHERE f.code_project_id=r.id ORDER BY path) AS folder_paths'


def _in_order(rows: list[dict], ids: list[UUID], what: str) -> list[dict]:
    """Rows in the order they were chosen; an id that is not available in this workspace refuses the request."""
    found = {row['id']: row for row in rows}
    if len(found) != len(ids):
        raise HTTPException(400, f'Select available {what}.')
    return [found[item] for item in ids]


def subjects(conn, project: UUID, value) -> dict:
    """The chosen code projects, environments, services and servers with what the message says about them. Code project
    folders are container paths under CODE_ROOT; archived code projects are never linked."""
    code_root: list[PurePosixPath] = []

    def paths(folder_paths: list[str]) -> list[str]:
        if folder_paths and not code_root:
            root = PurePosixPath(os.environ.get('CODE_ROOT', ''))
            if not root.is_absolute() or '..' in root.parts:
                raise HTTPException(503, 'CODE_ROOT must be an absolute mounted path')
            code_root.append(root)
        return [str(code_root[0] / path) for path in folder_paths]

    code = _in_order(conn.execute(f'''SELECT r.id,r.name,{FOLDER_PATHS} FROM ka.repositories r
        WHERE r.project_id=%s AND r.id=ANY(%s) AND r.archived_at IS NULL''', (project, value.code_project_ids)).fetchall(),
        value.code_project_ids, 'code projects')
    for item in code:
        if not item['folder_paths']:
            raise HTTPException(400, f"The code project {item['name']} has no folders.")
    environments = _in_order(conn.execute('''SELECT id,name,description,services,servers FROM ka.environment_assets
        WHERE project_id=%s AND id=ANY(%s)''', (project, value.environment_ids)).fetchall(), value.environment_ids, 'environments')
    services = _in_order(conn.execute('''SELECT id,name,description,deployments FROM ka.service_assets
        WHERE project_id=%s AND id=ANY(%s)''', (project, value.service_ids)).fetchall(), value.service_ids, 'services')
    servers = _in_order(conn.execute('SELECT id,name,hostname,description FROM ka.servers WHERE project_id=%s AND id=ANY(%s)',
                                     (project, value.server_ids)).fetchall(), value.server_ids, 'servers')
    members = {e['id']: [UUID(s['id']) for s in e['services']] for e in environments}
    linked: dict[UUID, list[dict]] = {}
    for row in conn.execute(f'''SELECT sr.service_id,r.id,r.name,{FOLDER_PATHS} FROM ka.service_repositories sr
            JOIN ka.repositories r ON r.id=sr.repository_id WHERE sr.service_id=ANY(%s) AND r.archived_at IS NULL
            ORDER BY lower(r.name),r.id''',
            ([item for ids in members.values() for item in ids] + [s['id'] for s in services],)).fetchall():
        linked.setdefault(row['service_id'], []).append({'id': row['id'], 'name': row['name'], 'paths': paths(row['folder_paths'])})
    deployments: dict[UUID, list[str]] = {}
    for row in conn.execute('''SELECT d.environment_id,s.name AS service,h.name AS server FROM ka.service_deployments d
            JOIN ka.services s ON s.id=d.service_id JOIN ka.servers h ON h.id=d.server_id
            WHERE d.environment_id=ANY(%s) ORDER BY lower(s.name),lower(h.name),d.runtime_name,d.id''',
            ([e['id'] for e in environments],)).fetchall():
        deployments.setdefault(row['environment_id'], []).append(f"{row['service']} on {row['server']}")

    def environment_code(environment_id: UUID) -> list[dict]:
        seen, items = set(), []
        for service_id in members[environment_id]:
            for item in linked.get(service_id, []):
                if item['id'] not in seen:
                    seen.add(item['id'])
                    items.append(item)
        return items

    return jsonable_encoder({
        'code_projects': [{'id': item['id'], 'name': item['name'], 'paths': paths(item['folder_paths'])} for item in code],
        'environments': [{'id': e['id'], 'name': e['name'], 'description': e['description'],
                          'servers': [f"{s['name']} ({s['hostname']})" for s in e['servers']],
                          'services': [s['name'] for s in e['services']], 'deployments': deployments.get(e['id'], []),
                          'code_projects': environment_code(e['id'])} for e in environments],
        'services': [{'id': s['id'], 'name': s['name'], 'description': s['description'],
                      'deployments': [f"{d['server']['name']} ({d['environment']})" for d in s['deployments']],
                      'code_projects': linked.get(s['id'], [])} for s in services],
        'servers': servers, 'instructions': value.instructions})


def _code(item: dict) -> str:
    return f"{item['name']} ({', '.join(item['paths'])})" if item['paths'] else item['name']


def _line(name: str, description: str, parts: list[tuple[str, list[str]]]) -> str:
    text = f'- {name}' + (f': {description.strip()}' if description.strip() else '')
    details = [label + ', '.join(values) for label, values in parts if values]
    return text + (' — ' + '; '.join(details) if details else '')


def first_message(context: dict[str, Any]) -> str:
    """The message the form sends, as a user could have typed it: what to document, the instructions, and how to answer
    so that the answer becomes the draft."""
    sections = ['Document the following.']
    if context['code_projects']:
        sections.append('## Code\n' + '\n'.join(f"- {p['name']} — {', '.join(p['paths'])}" for p in context['code_projects']))
    if context['environments']:
        sections.append('## Environments\n' + '\n'.join(_line(e['name'], e['description'], [
            ('servers: ', e['servers']), ('services: ', e['services']), ('deployments: ', e['deployments']),
            ('code: ', [_code(p) for p in e['code_projects']])]) for e in context['environments']))
    if context['services']:
        sections.append('## Services\n' + '\n'.join(_line(s['name'], s['description'], [
            ('deployed on ', s['deployments']), ('code: ', [_code(p) for p in s['code_projects']])]) for s in context['services']))
    if context['servers']:
        sections.append('## Servers\n' + '\n'.join(
            f"- {s['name']} ({s['hostname']})" + (f": {s['description'].strip()}" if s['description'].strip() else '')
            for s in context['servers']))
    if context['instructions']:
        sections.append('## Instructions\n' + context['instructions'])
    sections.append(FINAL_ANSWER)
    return '\n\n'.join(sections)
```

`services/admin-api/src/knowledge_admin/claude_code.py`: delete `first_message` (the function and its docstring);
keep `FINAL_ANSWER`. The module docstring becomes
`"""Claude Code sessions: fixed tools, the built-in system prompt, the final-answer rule and idle closing (admin-api and worker)."""`.

`services/admin-api/src/knowledge_admin/claude_code_api.py`:
- imports: `from knowledge_admin.claude_code import agent_system_prompt, close_idle`;
  `from knowledge_admin.data_root import data_dir, documentation_folder`;
  add `from knowledge_admin.generate_documents import first_message, subjects`; remove `import os` (no other use);
- replace `NOT_CLAUDE`, `NO_CONTEXT` and `PROMPT_CHANGED` with:

```python
NOTHING_CHOSEN = 'Choose at least one code project, environment, service or server to document.'
NO_FOLDER = ('Claude Code needs at least one working folder: choose code projects, or environments or services with '
             'code projects, or give the agent code projects.')
AGENT_MODEL = 'The agent has no enabled Claude model. Choose one on its General tab.'
```

- replace `SessionInput` with:

```python
class SessionInput(BaseModel):
    """Docs → Generate documents → New. The agent runs with its own model, prompt, instructions, skills and tools."""
    model_config = ConfigDict(extra='forbid')
    agent_id: str = Field(min_length=1, max_length=100)
    code_project_ids: list[UUID] = Field(default_factory=list, max_length=20)
    environment_ids: list[UUID] = Field(default_factory=list, max_length=20)
    service_ids: list[UUID] = Field(default_factory=list, max_length=50)
    server_ids: list[UUID] = Field(default_factory=list, max_length=50)
    instructions: str = Field(default='', max_length=20000)
    document_name: str = Field(max_length=240)
    folder: str = Field(max_length=200)

    @model_validator(mode='after')
    def valid(self):
        if '\x00' in self.instructions or '\x00' in self.document_name:
            raise ValueError('Text cannot contain null characters')
        self.instructions = self.instructions.strip()
        self.document_name = self.document_name.strip()
        if not document_slug(self.document_name):
            raise ValueError('Use letters or digits in the document name')
        self.folder = documentation_folder(self.folder)
        for name in ('code_project_ids', 'environment_ids', 'service_ids', 'server_ids'):
            setattr(self, name, list(dict.fromkeys(getattr(self, name))))
        if not (self.code_project_ids or self.environment_ids or self.service_ids or self.server_ids):
            raise ValueError(NOTHING_CHOSEN)
        return self
```

- replace `session_context` with:

```python
def session_context(conn, project: UUID, value: SessionInput, agent_projects: list[dict]) -> dict:
    """What to document (generate_documents.subjects) plus the working folders: the chosen code projects, the code
    projects of the chosen environments and services, then the agent's own code projects, each folder once. Every
    folder must exist now, inside DATA_ROOT, without a symbolic link on the way."""
    context = subjects(conn, project, value)
    candidates = [(path, item, False) for item in context['code_projects'] for path in item['paths']]
    candidates += [(path, item, False) for group in ('environments', 'services') for subject in context[group]
                   for item in subject['code_projects'] for path in item['paths']]
    candidates += [(path, {'id': str(item['id']), 'name': item['name']}, True)
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
    if not folders:
        raise HTTPException(400, NO_FOLDER)
    return {**context, 'folders': folders, 'code_project_ids': [item['id'] for item in context['code_projects']]}
```

- in `create_session`, replace everything from `model_id = value.model_id or …` to the `first_message(…)` insert with:

```python
            model_id = (config['model'] or {}).get('id')  # the agent's own model; the form has none
            model = conn.execute('SELECT id,provider,model,api_format,enabled FROM ka.llm_models WHERE id=%s FOR SHARE',
                                 (model_id,)).fetchone() if model_id else None
            if model is None or not model['enabled'] or not claude_model(model['provider'], model['api_format'], model['model']):
                raise HTTPException(400, AGENT_MODEL)
            context = session_context(conn, project, value, config['code']['projects'] if config['code']['enabled'] else [])
            snapshot = jsonable_encoder({'system_prompt': agent_system_prompt(config),
                                         'claude_code_tools': config['claude_code_tools'],
                                         'agent_version': config.get('config_version')})
            session_id = uuid4()
            conn.execute("""INSERT INTO ka.agent_sessions_cc(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,
                context_snapshot,document_name,folder) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (session_id, project, agent['id'], actor, model['id'], value.document_name, Jsonb(snapshot), Jsonb(context),
                 value.document_name, value.folder))
            conn.execute('INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text) VALUES (%s,1,%s,%s)',
                         (session_id, actor, first_message(context)))
```

  (the agent lookup, the config load, the audit and `return session_detail(…)` stay as they are; the
  documentation prompt lookup and the `PROMPT_CHANGED` check go).
- `from knowledge_admin.data_root import …`: `data_folder` and `data_path` are no longer used; keep `data_dir` and
  `documentation_folder`.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_claude_code_sessions.py tests/test_claude_code_runner.py tests/test_claude_code_worker.py tests/test_claude_code_agents.py tests/test_knowledge_agents_config.py -q`
Expected: PASS (the runner's sessions start from the new fixture; the worker still reads `folders`,
`system_prompt` and `claude_code_tools`).

Run: `cd services/admin-api && git grep -n "first_message\|PROMPT_CHANGED\|NOT_CLAUDE\|NO_CONTEXT\|documentation_prompts" -- src/knowledge_admin/claude_code_api.py src/knowledge_admin/claude_code.py`
Expected: only `from knowledge_admin.generate_documents import first_message, subjects` and its call.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/generate_documents.py services/admin-api/src/knowledge_admin/claude_code_api.py \
  services/admin-api/src/knowledge_admin/claude_code.py services/admin-api/tests/test_claude_code_sessions.py
git commit -m "feat(admin-api): Generate documents sends what to document as one message with the agent's own model

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Event history — older pages of a session's activity

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/claude_code_api.py` (`HISTORY_LIMIT`, `history` route)
- Modify: `services/admin-api/tests/test_claude_code_sessions.py`

**Interfaces:**
- Consumes: `stream_event(row)` (the stream's redaction).
- Produces: `GET /api/claude-code/sessions/{id}/events/history?before=<event id>&limit=<1..500, default 100>` →
  `{'events': [event, …] (oldest first), 'has_more': bool}`.

- [ ] **Step 1: Write the failing test**

Add to `services/admin-api/tests/test_claude_code_sessions.py` (after `test_event_stream_replays_history_and_follows_a_fake_worker`):

```python
def test_event_history_pages_backwards_like_the_stream(sessions):
    client, store = sessions.client, sessions.store
    sid = start(sessions)['id']
    other = start(sessions)['id']
    with store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions_cc SET status='waiting',turns=2 WHERE id=ANY(%s::uuid[])", ([sid, other],))
        for seq in (1, 2, 3):
            record(conn, sid, 1, seq, 'message', {'text': f'one {seq}'})
        record(conn, other, 1, 1, 'message', {'text': 'another session'})
        record(conn, sid, 2, 1, 'tool_result', {'name': 'Read', 'output': 'the whole file body', 'preview': 'line'})
        record(conn, sid, 2, 2, 'message', {'text': 'two 2'})
    url = f'/api/claude-code/sessions/{sid}/events/history'
    latest = client.get(url + '?limit=2', headers=HEADERS)
    assert latest.status_code == 200 and latest.headers['cache-control'] == 'no-store'
    page = latest.json()
    assert [(e['turn'], e['seq'], e['kind']) for e in page['events']] == [(2, 1, 'tool_result'), (2, 2, 'message')]
    assert page['has_more'] is True
    assert page['events'][0]['payload'] == {'name': 'Read', 'preview': 'line'}  # redacted like the stream
    assert 'the whole file body' not in latest.text
    older = client.get(f"{url}?before={page['events'][0]['id']}&limit=2", headers=HEADERS).json()
    assert [e['payload']['text'] for e in older['events']] == ['one 2', 'one 3'] and older['has_more'] is True
    first = client.get(f"{url}?before={older['events'][0]['id']}&limit=2", headers=HEADERS).json()
    assert [e['payload']['text'] for e in first['events']] == ['one 1'] and first['has_more'] is False
    default = client.get(url, headers=HEADERS).json()  # the latest 100: the whole session here, never another one's
    assert [e['seq'] for e in default['events']] == [1, 2, 3, 1, 2] and default['has_more'] is False
    assert all(e['id'] == r['id'] for e, r in zip(default['events'][-2:], page['events']))
    for bad in ('?limit=0', '?limit=501', '?before=0', '?before=x'):
        assert client.get(url + bad, headers=HEADERS).status_code == 400, bad
    assert client.get(f'/api/claude-code/sessions/{uuid4()}/events/history', headers=HEADERS).status_code == 404
    assert client.get(url).status_code == 403
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_claude_code_sessions.py -q -k history`
Expected: FAIL: `GET …/events/history` answers 404 (no such route yet), so `latest.status_code == 200` fails.

- [ ] **Step 3: Implement**

In `services/admin-api/src/knowledge_admin/claude_code_api.py`, under `KEEPALIVE_SECONDS = …`, add
`HISTORY_LIMIT = 100  # events per page; the session page opens with the latest page`, and inside `build_api`, before
`@router.get('/sessions/{session_id}/events')`, add:

```python
    @router.get('/sessions/{session_id}/events/history')
    def history(session_id: UUID, response: Response, before: int | None = Query(None, ge=1),
                limit: int = Query(HISTORY_LIMIT, ge=1, le=500)) -> dict:
        """A page of the session's events, oldest first: the latest page, or the page before an event id. Each event is
        what the stream sends (a tool result keeps its preview; the full output stays in the run record)."""
        response.headers['Cache-Control'] = 'no-store'
        db = database()
        with db.connect() as conn:
            row = conn.execute('SELECT turns FROM ka.agent_sessions_cc WHERE id=%s AND project_id=%s',
                               (session_id, db.project(conn, app_name))).fetchone()
            if row is None:
                raise HTTPException(404, 'Session not found.')
            keys = [f'cc:{session_id}:{turn}' for turn in range(1, row['turns'] + 1)]
            rows = conn.execute('''SELECT id,run_key,seq,kind,payload,created_at FROM ka.agent_run_events
                WHERE run_key=ANY(%s) AND (%s::bigint IS NULL OR id<%s) ORDER BY id DESC LIMIT %s''',
                (keys, before, before, limit + 1)).fetchall()
        return jsonable_encoder({'events': [stream_event(item) for item in reversed(rows[:limit])],
                                 'has_more': len(rows) > limit})
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_claude_code_sessions.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/claude_code_api.py services/admin-api/tests/test_claude_code_sessions.py
git commit -m "feat(admin-api): page backwards through a session's events, redacted like the stream

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: admin-ui — the Generate documents form

**Files:**
- Modify: `services/admin-ui/app/lib/claudeCode.ts` (`SessionInput`, `SessionDetail` snapshots)
- Modify: `services/admin-ui/app/components/ClaudeCodeSessionForm.tsx` (rewritten)
- Modify: `services/admin-ui/app/components/__tests__/claudeCodeSessionForm.test.tsx` (rewritten)

**Interfaces:**
- Consumes: Task 3's request body.
- Produces: `SessionInput { agent_id; code_project_ids; environment_ids; service_ids; server_ids; instructions;
  document_name; folder }`; `ClaudeCodeSessionForm({ onCreated, initialAgentId })` (same props as today, so
  `NewDocumentationWorkflow` keeps mounting it until Task 6).

- [ ] **Step 1: Write the failing tests**

Replace `services/admin-ui/app/components/__tests__/claudeCodeSessionForm.test.tsx` with:

```tsx
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, expect, test, vi } from 'vitest';
import { NewDocumentationWorkflow } from '../DocumentationWorkflowView';

afterEach(() => vi.unstubAllGlobals());
const NO_FOLDER = 'Claude Code needs at least one working folder: choose code projects, or environments or services with code projects, or give the agent code projects.';
const bodies: Record<string, unknown> = {
  '/api/llm': { models: [], assignments: { search_model_id: null, code_model_id: null, revision: 1 } },
  '/api/agents': { agents: [
    { id: 'api-writer', name: 'API writer', runtime: 'claude_code', enabled: true, model_id: 'claude', model_name: 'Claude on Bedrock' },
    { id: 'writer', name: 'Technical writer', runtime: 'claude_code', enabled: true, model_id: 'claude', model_name: 'Claude on Bedrock' },
    { id: 'no-model', name: 'Unfinished writer', runtime: 'claude_code', enabled: true, model_id: null, model_name: null },
    { id: 'retired', name: 'Retired writer', runtime: 'claude_code', enabled: false, model_id: 'claude', model_name: 'Claude on Bedrock' },
    { id: 'search', name: 'Search agent', runtime: 'openai', enabled: true, model_id: 'gpt', model_name: 'GPT' }] },
  '/api/code-projects': { projects: [{ id: 'repo', name: 'Payments API' }, { id: 'worker', name: 'Payments worker' }] },
  '/api/environments': { environments: [{ id: 'env', name: 'Production', services: [], servers: [] }] },
  '/api/services': { services: [{ id: 'svc', name: 'Payments' }] },
  '/api/servers': { servers: [{ id: 'srv', name: 'prod-1', hostname: '10.0.0.5', description: 'Payments host' }] },
  '/api/documentation/folders': { generated_folder: 'generated-documents', folders: [] },
};

function setup(error?: string, path = '/documentation/generations/new?scope=agent') {
  const posts: unknown[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/claude-code/sessions' && init?.method === 'POST') {
      posts.push(JSON.parse(init.body as string));
      return error ? Response.json({ error: { message: error } }, { status: 400 }) : Response.json({ id: 'session-1' }, { status: 201 });
    }
    return Response.json(bodies[url] ?? {});
  }));
  render(<MemoryRouter initialEntries={[path]}><Routes>
    <Route path="/documentation/generations/new" element={<NewDocumentationWorkflow />} />
    <Route path="/documentation/sessions/:sessionId" element={<div>Session page</div>} />
  </Routes></MemoryRouter>);
  return posts;
}
const check = (name: string) => fireEvent.click(screen.getByRole('checkbox', { name }));
async function select(label: string, option: string) {
  fireEvent.click(await screen.findByLabelText(label));
  fireEvent.click(await screen.findByRole('option', { name: option }));
}

test('Generate starts a session from an agent, what to document and instructions, and opens it', async () => {
  const posts = setup();
  fireEvent.click(await screen.findByLabelText('Agent'));
  expect(await screen.findByRole('option', { name: 'Technical writer' })).toBeVisible();
  expect(screen.queryByRole('option', { name: 'Retired writer' })).toBeNull();  // disabled
  expect(screen.queryByRole('option', { name: 'Search agent' })).toBeNull();  // native agents arrive with step 3
  fireEvent.click(screen.getByRole('option', { name: 'Technical writer' }));
  expect(screen.getByText(/own model, prompt, instructions, skills and tools \(Claude on Bedrock\)/)).toBeVisible();
  for (const removed of ['Model', 'Prompt', 'Notes', 'Comments']) expect(screen.queryByLabelText(removed)).toBeNull();
  expect(screen.queryByRole('button', { name: 'Choose a folder' })).toBeNull();
  fireEvent.change(screen.getByLabelText('Document name'), { target: { value: 'Payments – API reference' } });
  expect(screen.getByRole('button', { name: 'Generate' })).toBeDisabled();  // nothing to document yet
  check('Code');
  expect(screen.getByRole('button', { name: 'Generate' })).toBeDisabled();  // a choice without items is not enough
  check('Payments API');
  expect(screen.getByLabelText('Documentation folder')).toHaveValue('payments-api-docs');
  check('Environment'); check('Production');
  check('Services'); check('Payments');
  check('Servers'); check('prod-1 (10.0.0.5)');
  fireEvent.change(screen.getByLabelText('Instructions'), { target: { value: 'Focus on the public API.' } });
  fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
  expect(await screen.findByText('Session page')).toBeVisible();
  expect(posts).toEqual([{ agent_id: 'writer', code_project_ids: ['repo'], environment_ids: ['env'], service_ids: ['svc'],
    server_ids: ['srv'], instructions: 'Focus on the public API.', document_name: 'Payments – API reference', folder: 'payments-api-docs' }]);
});

test('an unchecked choice sends none of its items; a bad folder blocks Generate; refusals are shown', async () => {
  const posts = setup(NO_FOLDER);
  await select('Agent', 'Technical writer');
  fireEvent.change(screen.getByLabelText('Document name'), { target: { value: 'Hosts' } });
  check('Code'); check('Payments API'); check('Code');  // unchecked again: the project is not sent
  expect(screen.queryByRole('checkbox', { name: 'Payments API' })).toBeNull();
  check('Servers'); check('prod-1 (10.0.0.5)');
  fireEvent.change(screen.getByLabelText('Documentation folder'), { target: { value: 'Hosts' } });
  expect(screen.getByRole('button', { name: 'Generate' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Documentation folder'), { target: { value: 'hosts' } });
  fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
  expect(await screen.findByText(NO_FOLDER)).toBeVisible();
  expect(posts).toEqual([{ agent_id: 'writer', code_project_ids: [], environment_ids: [], service_ids: [], server_ids: ['srv'],
    instructions: '', document_name: 'Hosts', folder: 'hosts' }]);
});

test('the Test tab link preselects the agent; an agent without a model cannot generate', async () => {
  setup(undefined, '/documentation/generations/new?scope=agent&agent=no-model');
  expect(await screen.findByLabelText('Agent')).toHaveTextContent('Unfinished writer');
  expect(screen.getByText('This agent has no model. Choose a Claude model on its General tab.')).toBeVisible();
  fireEvent.change(screen.getByLabelText('Document name'), { target: { value: 'Payments' } });
  check('Code'); check('Payments API');
  expect(screen.getByRole('button', { name: 'Generate' })).toBeDisabled();
  await select('Agent', 'API writer');
  expect(screen.getByRole('button', { name: 'Generate' })).toBeEnabled();
});

test('an unknown agent in the link is ignored', async () => {
  setup(undefined, '/documentation/generations/new?scope=agent&agent=missing');
  expect(await screen.findByLabelText('Agent')).toHaveTextContent('Select…');
});
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-ui && pnpm exec vitest run app/components/__tests__/claudeCodeSessionForm.test.tsx`
Expected: FAIL: the form still shows **Model** and **Prompt** and has no **Code** checkbox.

- [ ] **Step 3: Implement**

`services/admin-ui/app/lib/claudeCode.ts`: replace `SessionInput` and the two snapshot fields of `SessionDetail`:

```ts
export interface SessionDetail extends SessionSummary {
  document_name: string; draft: string | null; draft_turn: number | null; model_id: string;
  /** The agent's own instructions when the session started (earlier sessions also hold their documentation prompt). */
  prompt_snapshot: { system_prompt: string; claude_code_tools?: string[]; agent_version?: { id: string; version: number; label: string } | null };
  /** What to document and the working folders (earlier sessions hold servers and notes instead of the new keys). */
  context_snapshot: { folders: SessionFolder[]; code_project_ids: string[]; instructions?: string };
  inputs: { seq: number; text: string; status: 'queued' | 'delivered' | 'discarded'; turn: number | null; created_at: string }[];
}
```

```ts
/** Docs → Generate documents → New: the agent runs with its own model, prompt, instructions, skills and tools. */
export interface SessionInput {
  agent_id: string; code_project_ids: string[]; environment_ids: string[]; service_ids: string[]; server_ids: string[];
  instructions: string; document_name: string; folder: string;
}
```

Replace `services/admin-ui/app/components/ClaudeCodeSessionForm.tsx` with:

```tsx
import { useEffect, useState } from 'react';
import { ApiError, getEnvironments, getServers, getServices } from '~/lib/api';
import { createSession } from '~/lib/claudeCode';
import { getCodeProjects } from '~/lib/codeProjects';
import { defaultFolder, documentSlug, folderError, getDocumentationFolders } from '~/lib/documents';
import { getAgents, type AgentSummary } from '~/lib/profiles';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Button } from '~/components/ui/button';
import { Checkbox } from '~/components/ui/checkbox';
import { Field, FieldContent, FieldDescription, FieldLabel, FieldLegend, FieldSet } from '~/components/ui/field';
import { Input } from '~/components/ui/input';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '~/components/ui/select';
import { Skeleton } from '~/components/ui/skeleton';
import { Textarea } from '~/components/ui/textarea';

type Kind = 'code' | 'environments' | 'services' | 'servers';
type Item = { id: string; name: string };
type Data = { agents: AgentSummary[]; items: Record<Kind, Item[]>; folders: string[] };
const KINDS: { kind: Kind; label: string; legend: string }[] = [
  { kind: 'code', label: 'Code', legend: 'Code projects' }, { kind: 'environments', label: 'Environment', legend: 'Environments' },
  { kind: 'services', label: 'Services', legend: 'Services' }, { kind: 'servers', label: 'Servers', legend: 'Servers' }];
const NOTHING: Record<Kind, string[]> = { code: [], environments: [], services: [], servers: [] };

/** Docs → Generate documents → New: an agent, what to document, instructions, and the document's name and folder.
 * There is no model or prompt: the agent runs with its own. */
export default function ClaudeCodeSessionForm({ onCreated, initialAgentId = '' }: { onCreated: (id: string) => void; initialAgentId?: string }) {
  const [data, setData] = useState<Data | null>(null);
  const [agentId, setAgentId] = useState(initialAgentId);
  const [kinds, setKinds] = useState<Kind[]>([]);
  const [chosen, setChosen] = useState<Record<Kind, string[]>>(NOTHING);
  const [instructions, setInstructions] = useState('');
  const [name, setName] = useState('');
  const [folder, setFolder] = useState('docs');
  const [folderTouched, setFolderTouched] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    // The documentation folder list only feeds suggestions: a failing request must not block the form.
    Promise.all([getAgents(), getCodeProjects(), getEnvironments(), getServices(), getServers(),
      getDocumentationFolders().catch(() => ({ folders: [] as { folder: string }[] }))])
      .then(([agents, projects, environments, services, servers, docFolders]) => {
        if (cancelled) return;
        const claudeAgents = agents.agents.filter(agent => agent.runtime === 'claude_code' && agent.enabled);
        setData({ agents: claudeAgents, folders: docFolders.folders.map(item => item.folder), items: {
          code: projects.projects, environments: environments.environments, services: services.services,
          servers: servers.servers.map(server => ({ id: server.id, name: `${server.name} (${server.hostname})` })) } });
        if (!claudeAgents.some(agent => agent.id === initialAgentId)) setAgentId('');
      }).catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : 'Could not load agents and what to document'); });
    return () => { cancelled = true; };
  }, [initialAgentId]);

  const picked = (kind: Kind) => kinds.includes(kind) ? chosen[kind] : [];
  const toggleKind = (kind: Kind, checked: boolean) => setKinds(current => checked ? [...current, kind] : current.filter(item => item !== kind));
  function toggle(kind: Kind, id: string, checked: boolean) {
    const next = checked ? [...chosen[kind], id] : chosen[kind].filter(item => item !== id);
    setChosen(current => ({ ...current, [kind]: next }));
    if (kind === 'code' && !folderTouched) {
      const first = data?.items.code.find(project => project.id === next[0]);
      setFolder(first ? defaultFolder(first.name) : 'docs');
    }
  }
  const agent = data?.agents.find(item => item.id === agentId);
  const target = folder.trim();
  const badFolder = target ? folderError(target) : '';
  const ready = Boolean(agent?.model_id && KINDS.some(({ kind }) => picked(kind).length > 0) && documentSlug(name) && target && !badFolder);

  async function submit() {
    if (!ready || pending) return;
    setPending(true); setError('');
    try {
      const session = await createSession({ agent_id: agentId, code_project_ids: picked('code'), environment_ids: picked('environments'),
        service_ids: picked('services'), server_ids: picked('servers'), instructions, document_name: name.trim(), folder: target });
      onCreated(session.id);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not start generating');
      setPending(false);
    }
  }
  function check(id: string, label: string, checked: boolean, change: (checked: boolean) => void) {
    return <Field key={id} orientation="horizontal"><Checkbox id={id} checked={checked} onCheckedChange={value => change(value === true)} />
      <FieldContent><FieldLabel htmlFor={id}>{label}</FieldLabel></FieldContent></Field>;
  }
  if (!data) return error ? <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert> : <Skeleton className="h-48 w-full" />;
  return <form className="flex max-w-2xl flex-col gap-5" onSubmit={e => { e.preventDefault(); void submit(); }}>
    <fieldset disabled={pending} className="flex min-w-0 flex-col gap-5">
      <Field><FieldLabel htmlFor="generate-agent">Agent</FieldLabel>
        <Select value={agentId || 'none'} onValueChange={value => setAgentId(value === 'none' ? '' : value)}>
          <SelectTrigger id="generate-agent"><SelectValue /></SelectTrigger>
          <SelectContent><SelectGroup><SelectItem value="none">Select…</SelectItem>
            {data.agents.map(item => <SelectItem key={item.id} value={item.id}>{item.name}</SelectItem>)}</SelectGroup></SelectContent></Select>
        <FieldDescription className={agent && !agent.model_id ? 'text-destructive' : undefined}>{data.agents.length === 0
          ? 'Create one in Agents → New agent with Agent type Claude Code.'
          : agent && !agent.model_id ? 'This agent has no model. Choose a Claude model on its General tab.'
          : `The agent works with its own model, prompt, instructions, skills and tools${agent?.model_name ? ` (${agent.model_name})` : ''}.`}</FieldDescription></Field>
      <FieldSet><FieldLegend>What to document</FieldLegend>
        <FieldDescription>Code projects, and the code projects of the environments and services you choose, become the agent's read-only working folders. Environments, services and servers are described to the agent as text; this form gives no server access.</FieldDescription>
        {KINDS.map(({ kind, label, legend }) => <div key={kind} className="flex flex-col gap-2">
          {check(`what-${kind}`, label, kinds.includes(kind), value => toggleKind(kind, value))}
          {kinds.includes(kind) && <FieldSet className="ml-6"><FieldLegend variant="label">{legend}</FieldLegend>
            {data.items[kind].length === 0 && <FieldDescription>Nothing to choose yet.</FieldDescription>}
            {data.items[kind].map(item => check(`generate-${kind}-${item.id}`, item.name, chosen[kind].includes(item.id), value => toggle(kind, item.id, value)))}
          </FieldSet>}
        </div>)}
      </FieldSet>
      <Field><FieldLabel htmlFor="generate-instructions">Instructions</FieldLabel><Textarea id="generate-instructions" rows={3} maxLength={20000} value={instructions} onChange={e => setInstructions(e.target.value)} placeholder="Optional: extra instructions for this run" /></Field>
      <Field><FieldLabel htmlFor="generate-document-name">Document name</FieldLabel><Input id="generate-document-name" maxLength={240} value={name} onChange={e => setName(e.target.value)} placeholder="Payments – API reference" />
        {name && !documentSlug(name) && <FieldDescription className="text-destructive">Use letters or digits in the name.</FieldDescription>}</Field>
      <Field><FieldLabel htmlFor="generate-folder">Documentation folder</FieldLabel><Input id="generate-folder" list="generate-documentation-folders" maxLength={200} value={folder} onChange={e => { setFolderTouched(true); setFolder(e.target.value); }} />
        <datalist id="generate-documentation-folders">{data.folders.map(item => <option key={item} value={item} />)}</datalist>
        <FieldDescription className={badFolder ? 'text-destructive' : undefined}>{badFolder || `Save document stores it as ${target || '<folder>'}/${documentSlug(name) || '<document>'}/README.md in the generated documents folder.`}</FieldDescription></Field>
    </fieldset>
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    <Button type="submit" className="self-start" disabled={pending || !ready}>{pending ? 'Starting…' : 'Generate'}</Button>
  </form>;
}
```

`ServiceAsset`, `EnvironmentAsset` and `CodeProject` all have `id` and `name`, so they fit `Item` directly.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd services/admin-ui && pnpm exec tsc --noEmit && pnpm exec vitest run app/components/__tests__/claudeCodeSessionForm.test.tsx app/components/__tests__/claudeCodeSession.test.tsx app/components/__tests__/agentTypes.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-ui/app/lib/claudeCode.ts services/admin-ui/app/components/ClaudeCodeSessionForm.tsx \
  services/admin-ui/app/components/__tests__/claudeCodeSessionForm.test.tsx
git commit -m "feat(admin-ui): Generate documents form: agent, what to document, instructions, name and folder

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: admin-ui — Generate documents replaces the one-shot pages and the Sessions menu

**Files:**
- Modify: `services/admin-ui/app/routes.ts`
- Modify: `services/admin-ui/app/routes/documentationGenerations.tsx`
- Create: `services/admin-ui/app/routes/documentationGenerationNew.tsx`
- Create: `services/admin-ui/app/routes/documentationGeneration.tsx`
- Modify: `services/admin-ui/app/routes/documentationSessions.tsx`, `documentationSession.tsx` (redirects)
- Delete: `services/admin-ui/app/routes/documentationWorkflow.tsx`, `documentationWorkflowNew.tsx`
- Delete: `services/admin-ui/app/components/DocumentationWorkflowView.tsx`, `DocumentationGenerationForm.tsx`,
  `DocumentationGenerationsView.tsx`, `CodeProjectDocumentationForm.tsx`, `CodeDocumentationPhase.tsx`,
  `CodeGraphPhase.tsx`, `ApiAnalysisProgress.tsx`, `DocumentationTokenUsage.tsx`; `app/hooks/useDocumentationDraft.ts`
- Delete: `services/admin-ui/app/components/__tests__/documentationGeneration.test.tsx`,
  `codeProjectDocumentation.test.tsx`, `apiAnalysisProgress.test.tsx`, `documentationTokenUsage.test.tsx`
- Modify: `services/admin-ui/app/components/ClaudeCodeSessionForm.tsx` (`NewGeneration`)
- Modify: `services/admin-ui/app/components/ClaudeCodeSessionsView.tsx`, `ClaudeCodeSessionView.tsx` (back link)
- Modify: `services/admin-ui/app/components/AdminLayout.tsx`, `AgentDetailView.tsx`, `DocumentEditorView.tsx`,
  `DocumentsView.tsx`, `StorageOverviewView.tsx`, `RunsView.tsx`
- Modify: `services/admin-ui/app/lib/documents.ts`, `lib/codeProjects.ts`
- Modify: `services/admin-ui/app/components/__tests__/claudeCodeSessions.test.tsx`, `claudeCodeSessionForm.test.tsx`,
  `claudeCodeSession.test.tsx`, `adminLayout.test.tsx`, `agentTypes.test.tsx`, `runs.test.tsx`
- Create: `services/admin-ui/app/components/__tests__/generateDocumentsRoutes.test.tsx`

**Interfaces:**
- Consumes: Task 5's form.
- Produces: `NewGeneration()` in `ClaudeCodeSessionForm.tsx` (reads `?agent=`, opens `/documentation/generations/<id>`);
  routes `/documentation/generations`, `/documentation/generations/new`, `/documentation/generations/:sessionId`;
  `/documentation/sessions` and `/documentation/sessions/:sessionId` redirect; `processCodeProject(id)`.

- [ ] **Step 1: Write the failing tests**

Replace the test in `services/admin-ui/app/components/__tests__/claudeCodeSessions.test.tsx` with:

```tsx
test('Docs → Generate documents lists generations with status, agent, tokens and document', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.startsWith('/api/claude-code/sessions')
    ? Response.json({ sessions: [row, { ...row, id: 's2', title: 'Jira overview', status: 'closed', document_id: null, document_title: null }] })
    : Response.json({ is_admin: true })));
  render(<MemoryRouter initialEntries={['/documentation/generations']}><AdminLayout><ClaudeCodeSessionsView /></AdminLayout></MemoryRouter>);
  const first = (await screen.findByRole('link', { name: 'Payments – API reference' })).closest('tr')!;
  expect(within(first).getByRole('link', { name: 'Payments – API reference' })).toHaveAttribute('href', '/documentation/generations/s1');
  expect(within(first).getByText('Waiting for you')).toBeVisible();
  expect(within(first).getByText('Technical writer')).toBeVisible();
  expect(within(first).getByText('12,345 in · 678 out')).toBeVisible();
  expect(within(first).getAllByRole('link').map(link => link.getAttribute('href'))).toContain('/documents/doc');
  const second = screen.getByRole('link', { name: 'Jira overview' }).closest('tr')!;
  expect(within(second).getByText('Finished')).toBeVisible();
  expect(within(second).getByText('Not saved')).toBeVisible();
  expect(screen.getByRole('link', { name: 'New' })).toHaveAttribute('href', '/documentation/generations/new');
  const docs = within(screen.getByRole('navigation', { name: 'Docs' }));
  expect(docs.getAllByRole('link').map(link => link.textContent)).toEqual(['Documents', 'Prompts', 'Generate documents', 'Folders']);
  expect(screen.getByRole('heading', { name: 'Generate documents', level: 1 })).toBeVisible();
  expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);  // the layout's heading only
});
```

Create `services/admin-ui/app/components/__tests__/generateDocumentsRoutes.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { expect, test } from 'vitest';
import SessionsRedirect from '~/routes/documentationSessions';
import SessionRedirect from '~/routes/documentationSession';

function open(path: string) {
  render(<MemoryRouter initialEntries={[path]}><Routes>
    <Route path="/documentation/sessions" element={<SessionsRedirect />} />
    <Route path="/documentation/sessions/:sessionId" element={<SessionRedirect />} />
    <Route path="/documentation/generations" element={<p>Generate documents list</p>} />
    <Route path="/documentation/generations/:sessionId" element={<p>Generation page</p>} />
  </Routes></MemoryRouter>);
}

test('old session links open the generation', async () => {
  open('/documentation/sessions/s1');
  expect(await screen.findByText('Generation page')).toBeVisible();
});

test('the old Sessions list opens Generate documents', async () => {
  open('/documentation/sessions');
  expect(await screen.findByText('Generate documents list')).toBeVisible();
});
```

In `services/admin-ui/app/components/__tests__/claudeCodeSessionForm.test.tsx`:
- import `{ NewGeneration } from '../ClaudeCodeSessionForm'` instead of `NewDocumentationWorkflow`;
- remove the `'/api/llm'` entry from `bodies`;
- `setup`'s default path becomes `'/documentation/generations/new'`, the routes become
  `<Route path="/documentation/generations/new" element={<NewGeneration />} />` and
  `<Route path="/documentation/generations/:sessionId" element={<div>Session page</div>} />`;
- the two link paths become `'/documentation/generations/new?agent=no-model'` and
  `'/documentation/generations/new?agent=missing'`.

In `services/admin-ui/app/components/__tests__/claudeCodeSession.test.tsx`, in
`the session page has one page heading, the session title`, change the entry to `'/documentation/generations/s1'` and
add after the heading assertions:

```tsx
  expect(screen.getByRole('link', { name: 'Generate documents' })).toHaveAttribute('href', '/documentation/generations');
```

In `services/admin-ui/app/components/__tests__/adminLayout.test.tsx`, replace
`expect(docs.getByRole("link", { name: "Sessions" })).toHaveAttribute("href", "/documentation/sessions");` with
`expect(docs.queryByRole("link", { name: "Sessions" })).toBeNull();`.

In `services/admin-ui/app/components/__tests__/agentTypes.test.tsx`, the expected href becomes
`'/documentation/generations/new?agent=writer'`.

In `services/admin-ui/app/components/__tests__/runs.test.tsx`, delete `documentation history links each job to its run
records` and the `DocumentationGenerationsView` import.

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-ui && pnpm exec vitest run app/components/__tests__/claudeCodeSessions.test.tsx app/components/__tests__/generateDocumentsRoutes.test.tsx app/components/__tests__/claudeCodeSessionForm.test.tsx app/components/__tests__/adminLayout.test.tsx app/components/__tests__/agentTypes.test.tsx`
Expected: FAIL: the list links to `/documentation/sessions/s1`, there is no **New** link, `NewGeneration` is not
exported, the routes do not redirect.

- [ ] **Step 3: Implement**

Delete the one-shot UI:

```bash
cd services/admin-ui
git rm app/routes/documentationWorkflow.tsx app/routes/documentationWorkflowNew.tsx \
  app/components/DocumentationWorkflowView.tsx app/components/DocumentationGenerationForm.tsx \
  app/components/DocumentationGenerationsView.tsx app/components/CodeProjectDocumentationForm.tsx \
  app/components/CodeDocumentationPhase.tsx app/components/CodeGraphPhase.tsx app/components/ApiAnalysisProgress.tsx \
  app/components/DocumentationTokenUsage.tsx app/hooks/useDocumentationDraft.ts \
  app/components/__tests__/documentationGeneration.test.tsx app/components/__tests__/codeProjectDocumentation.test.tsx \
  app/components/__tests__/apiAnalysisProgress.test.tsx app/components/__tests__/documentationTokenUsage.test.tsx
```

`app/routes.ts`: replace the five lines from `route("documentation/generations", …)` to
`route("documentation/sessions/:sessionId", …)` with:

```ts
  route("documentation/generations", "./routes/documentationGenerations.tsx"),
  route("documentation/generations/new", "./routes/documentationGenerationNew.tsx"),
  route("documentation/generations/:sessionId", "./routes/documentationGeneration.tsx"),
  route("documentation/sessions", "./routes/documentationSessions.tsx"),
  route("documentation/sessions/:sessionId", "./routes/documentationSession.tsx"),
```

`app/routes/documentationGenerations.tsx`:

```tsx
import AdminLayout from '../components/AdminLayout';
import ClaudeCodeSessionsView from '../components/ClaudeCodeSessionsView';

export default function Page() {
  return <AdminLayout><ClaudeCodeSessionsView /></AdminLayout>;
}
```

`app/routes/documentationGenerationNew.tsx`:

```tsx
import AdminLayout from '../components/AdminLayout';
import { NewGeneration } from '../components/ClaudeCodeSessionForm';

export default function Page() {
  return <AdminLayout><NewGeneration /></AdminLayout>;
}
```

`app/routes/documentationGeneration.tsx`:

```tsx
import { useParams } from 'react-router';
import AdminLayout from '../components/AdminLayout';
import ClaudeCodeSessionView from '../components/ClaudeCodeSessionView';

export default function Page() {
  const { sessionId } = useParams();
  return <AdminLayout><ClaudeCodeSessionView key={sessionId} sessionId={sessionId!} /></AdminLayout>;
}
```

`app/routes/documentationSessions.tsx`:

```tsx
import { Navigate } from 'react-router';

/** Sessions moved to Docs → Generate documents. */
export default function Page() {
  return <Navigate replace to="/documentation/generations" />;
}
```

`app/routes/documentationSession.tsx`:

```tsx
import { Navigate, useParams } from 'react-router';

/** A session link from before Generate documents: the same session under its new address. */
export default function Page() {
  const { sessionId } = useParams();
  return <Navigate replace to={`/documentation/generations/${encodeURIComponent(sessionId ?? '')}`} />;
}
```

`app/components/ClaudeCodeSessionForm.tsx`: change the first import to
`import { useEffect, useState } from 'react';` plus `import { useNavigate, useSearchParams } from 'react-router';`, and
append:

```tsx
/** Docs → Generate documents → New. ?agent=<id> (an agent's Test tab) preselects the agent; Generate opens the session. */
export function NewGeneration() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  return <ClaudeCodeSessionForm initialAgentId={params.get('agent') ?? ''} onCreated={id => navigate(`/documentation/generations/${id}`)} />;
}
```

`app/components/ClaudeCodeSessionsView.tsx`: the doc comment becomes
`/** Docs → Generate documents: agent sessions that write documents, newest first; leaving a session page does not stop it. */`;
the intro paragraph becomes `An agent documents the code, environments, services and servers you choose. Follow it
live, send messages, then save the draft as a document. Leaving the page does not stop it; runs idle for 24 hours are
closed and their history is kept.`; the button becomes
`<Button asChild><Link to="/documentation/generations/new">New</Link></Button>`; the first column header becomes
`Generation`; the row link becomes `` to={`/documentation/generations/${row.id}`} ``; the empty text becomes
`Nothing generated yet. Start with New.`; the error fallback becomes `'Could not load generations'`.

`app/components/ClaudeCodeSessionView.tsx`: the back link becomes
`<Link className="underline" to="/documentation/generations">Generate documents</Link>`.

`app/components/AdminLayout.tsx`:
- remove `MessagesSquare` from the `lucide-react` import;
- delete `const docsSessions = …`; `sessionPage` becomes
  `const sessionPage = /^\/documentation\/generations\/(?!new$)[^/]+$/.test(pathname);  // its own heading is the session title`;
- in `title`, replace `docsHistory ? "Generate documentation" : docsSessions ? "Documentation sessions" : ` with
  `docsHistory ? "Generate documents" : `;
- the heading condition becomes `{!sessionPage && <div …>`;
- delete the `Sessions` `SidebarMenuItem`.

`app/components/AgentDetailView.tsx` (Test card): the description's last sentence becomes `Choose what to document,
then follow the agent live, send messages, stop it and save the draft.` and the link becomes
`` <Link to={`/documentation/generations/new?agent=${encodeURIComponent(agentId)}`}>Start a session</Link> ``.

`app/components/DocumentEditorView.tsx`: delete the `{document?.workflows?.map(workflow => …)}` line.

`app/components/DocumentsView.tsx` and `app/components/StorageOverviewView.tsx`: the link text
`Generate documentation` becomes `Generate documents`. In `StorageOverviewView.tsx`, the `code_index` entry's `purpose`
becomes `'CodeGraph indexes and source hashes, generated from Code graphs. Agents read the code through their working folders.'`,
its `link` becomes `'/code-graphs'`; the first step's text becomes `Code projects map folders under the code root. Agents
read them as working folders when they generate documents.`

`app/components/RunsView.tsx`: `const KINDS = ['test', 'board', 'claude_code'];`.

`app/lib/documents.ts`: delete `DocumentationPhaseUsage`, `DocumentationGeneration`, `ApiAnalysis`, `getApiAnalysis`,
`getDocumentationGenerations`, `GenerationInput`, `generateDocumentation`, `PROMPT_CHANGED` (with its comment),
`CodeProjectGenerationInput`, `GenerationKind`, `DocumentationPromptSelection`, `DocumentationWorkflow`,
`getDocumentationWorkflows`, `getDocumentationWorkflow`, `createDocumentationWorkflow`, `saveDocumentationDraft`, and
the `workflows?: {id:string;name:string}[];` member of `DocumentDetail`.

`app/lib/codeProjects.ts`: `export const processCodeProject = (id: string) => request<{ job_id: string }>(`/api/code-projects/${encodeURIComponent(id)}/process`, { method: 'POST' });`.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd services/admin-ui && pnpm exec tsc --noEmit && pnpm test`
Expected: PASS (`tsc` finds no reference to a deleted module or function).

Run: `cd services/admin-ui && git grep -n "documentation/sessions\|scope=agent\|DocumentationWorkflow\|getDocumentationGenerations\|generateDocumentation\|workflow_id\|Generate documentation" -- app`
Expected: only `app/routes.ts` (the two redirect routes) and `generateDocumentsRoutes.test.tsx`.

- [ ] **Step 5: Commit**

```bash
git add -A services/admin-ui/app
git commit -m "feat(admin-ui): Generate documents lists and opens agent sessions; the one-shot pages and Sessions menu go

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: admin-ui — the session page fits the screen and pages through its activity

**Files:**
- Modify: `services/admin-ui/app/lib/claudeCode.ts` (`HISTORY_PAGE`, `getSessionHistory`, `sessionEventsUrl`)
- Modify: `services/admin-ui/app/components/ClaudeCodeSessionView.tsx` (rewritten)
- Modify: `services/admin-ui/app/components/__tests__/claudeCodeSession.test.tsx` (rewritten)

**Interfaces:**
- Consumes: Task 4's `GET …/events/history`; the existing `GET …/events?after=<id>` stream.
- Produces: `getSessionHistory(id, before?, signal?) → { events: SessionEvent[]; has_more: boolean }`,
  `sessionEventsUrl(id, after = 0)`.

- [ ] **Step 1: Write the failing tests**

Replace `services/admin-ui/app/components/__tests__/claudeCodeSession.test.tsx` with:

```tsx
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
vi.mock('../Markdown', () => ({ default: ({ content }: { content: string }) => <div data-testid="markdown">{content}</div> }));
import ClaudeCodeSessionView from '../ClaudeCodeSessionView';
import { SAME_DOCUMENT_NAME } from '~/lib/documents';

class Stream extends EventTarget {
  static instances: Stream[] = [];
  close = vi.fn();
  constructor(public url: string) { super(); Stream.instances.push(this); }
  emit(type: string, value: unknown) { this.dispatchEvent(new MessageEvent(type, { data: JSON.stringify(value) })); }
}
beforeEach(() => { Stream.instances = []; vi.stubGlobal('EventSource', Stream); });
afterEach(() => vi.unstubAllGlobals());
const state = { status: 'starting', status_message: '', turns: 0, input_tokens: 0, output_tokens: 0, cached_tokens: 0, cost_usd: 0,
  draft: null, draft_turn: null, document_id: null, worker_active: false, queued: 1 };
const detail = { ...state, id: 's1', title: 'Payments – API reference', document_name: 'Payments – API reference', folder: 'payments-api-docs',
  agent_id: 'writer', agent_name: 'Technical writer', model_id: 'claude', model_name: 'Claude on Bedrock', document_title: null,
  prompt_snapshot: { system_prompt: 'You are an expert technical writer.', claude_code_tools: ['Read'], agent_version: null },
  context_snapshot: { folders: [{ path: '/work/data/code/app', kind: 'code_project', name: 'Payments API', code_project_id: 'repo' }],
    code_project_ids: ['repo'], instructions: '' },
  inputs: [], created_at: '2026-10-07T10:00:00Z', last_activity_at: '2026-10-07T10:00:00Z', closed_at: null };
const message = (id: number) => ({ id, turn: 1, seq: id, kind: 'message', payload: { text: `Message ${id}` } });
const page = (events: unknown[], has_more = false) => Response.json({ events, has_more });
const opened = () => waitFor(() => { expect(Stream.instances).toHaveLength(1); return Stream.instances[0]; });

test('the session page streams activity, sends messages, stops, saves the draft and closes', async () => {
  const posts: { url: string; body?: unknown }[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url.includes('/events/history')) return page([]);
    if (init?.method !== 'POST') return Response.json(detail);
    const body = init.body ? JSON.parse(init.body as string) : undefined;
    posts.push({ url, body });
    if (url.endsWith('/messages')) return Response.json({ seq: 2, status: 'queued', working: true }, { status: 202 });
    if (url.endsWith('/stop')) return Response.json({ id: 's1', discarded: 0 }, { status: 202 });
    if (url.endsWith('/document')) return body.update_existing
      ? Response.json({ id: 'doc', revision: 2, version_id: 'v2', folder: 'payments-api-docs', output_folder: '/work/data/generated-documents/payments-api-docs/payments-api-reference', export_error: null }, { status: 201 })
      : Response.json({ error: { message: SAME_DOCUMENT_NAME } }, { status: 409 });
    return Response.json({ id: 's1' });
  }));
  render(<MemoryRouter><ClaudeCodeSessionView sessionId="s1" /></MemoryRouter>);
  expect(await screen.findByText('Starting')).toBeVisible();
  expect(screen.getByText(/Waiting for the Claude Code worker/)).toBeVisible();
  const stream = await opened();
  expect(stream.url).toBe('/api/claude-code/sessions/s1/events?after=0');  // nothing yet: the stream starts at the beginning
  expect(screen.getByText('Beginning of the session')).toBeVisible();
  act(() => {
    stream.emit('session', { ...state, status: 'working', turns: 1, worker_active: true, queued: 0 });
    stream.emit('run_event', { id: 1, turn: 1, seq: 3, kind: 'input', payload: { task: 'Document the payments API.' } });
    stream.emit('run_event', { id: 2, turn: 1, seq: 4, kind: 'reasoning', payload: { text: 'Start with the routes.' } });
    stream.emit('run_event', { id: 3, turn: 1, seq: 5, kind: 'tool_call', payload: { name: 'Read', summary: '/work/data/code/app/main.py' } });
    stream.emit('run_event', { id: 4, turn: 1, seq: 6, kind: 'tool_result', payload: { name: 'Bash', preview: 'total 8\ndrwxr-xr-x app', is_error: false } });
    stream.emit('run_event', { id: 4, turn: 1, seq: 6, kind: 'tool_result', payload: { name: 'Bash', preview: 'total 8\ndrwxr-xr-x app', is_error: false } });
  });
  expect(screen.getByText('Working')).toBeVisible();
  expect(screen.queryByText(/Waiting for the Claude Code worker/)).toBeNull();
  const activity = within(screen.getByRole('region', { name: 'Agent activity' }));
  expect(await activity.findByText('Document the payments API.')).toBeVisible();  // appended once per animation frame
  expect(activity.getByText('Start with the routes.')).toBeVisible();
  expect(activity.getByText('/work/data/code/app/main.py')).toBeVisible();
  expect(activity.getAllByText(/drwxr-xr-x app/)).toHaveLength(1);  // a replayed event is shown once
  expect(screen.getByRole('button', { name: 'Save document' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'Also cover retries.' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
  expect(await screen.findByText(/^Queued\./)).toBeVisible();
  expect(screen.getByLabelText('Message')).toHaveValue('');
  fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
  await waitFor(() => expect(posts.map(p => p.url)).toContain('/api/claude-code/sessions/s1/stop'));
  expect(await screen.findByText('Stopping the current turn…')).toBeVisible();
  act(() => {
    stream.emit('run_event', { id: 5, turn: 1, seq: 7, kind: 'message', payload: { text: '# Payments API' } });
    stream.emit('session', { ...state, status: 'waiting', turns: 1, worker_active: true, queued: 0, draft: '# Payments API\n\nRetries back off.',
      draft_turn: 1, input_tokens: 1050, output_tokens: 40, cached_tokens: 900 });
  });
  expect(screen.getByText('Waiting for you')).toBeVisible();
  expect(screen.queryByText('Stopping the current turn…')).toBeNull();  // the status changed: the notice is stale
  expect(within(screen.getByRole('region', { name: 'Draft' })).getByTestId('markdown')).toHaveTextContent('# Payments API Retries back off.');
  expect(screen.getByText('In 1,050 · Out 40 · Cached 900')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Stop' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Save document' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Save new version' }));
  expect(await screen.findByText(/Saved as version 2 in payments-api-docs/)).toBeVisible();
  expect(screen.getByRole('link', { name: 'Open document' })).toHaveAttribute('href', '/documents/doc');
  expect(posts.filter(p => p.url.endsWith('/document')).map(p => p.body)).toEqual([{ update_existing: false }, { update_existing: true }]);
  fireEvent.click(screen.getByRole('button', { name: 'Close session' }));
  await waitFor(() => expect(posts.map(p => p.url)).toContain('/api/claude-code/sessions/s1/close'));
  act(() => { stream.emit('session', { ...state, status: 'closed', turns: 1, draft: '# Payments API\n\nRetries back off.' }); stream.emit('closed', {}); });
  expect(screen.getByText('Finished')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Send message' })).toBeDisabled();
  expect(stream.close).toHaveBeenCalled();
});

test('a replayed batch of events is shown once and a stale session load does not undo the stream', async () => {
  let resolve: (response: Response) => void = () => {};
  vi.stubGlobal('fetch', vi.fn((url: string) => url.includes('/events/history') ? Promise.resolve(page([]))
    : new Promise<Response>(done => { resolve = done; })));
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => setTimeout(() => callback(0), 0));
  vi.stubGlobal('cancelAnimationFrame', (handle: number) => clearTimeout(handle));
  render(<MemoryRouter><ClaudeCodeSessionView sessionId="s1" /></MemoryRouter>);
  const stream = await opened();
  const batch = [1, 2, 3].map(message);
  act(() => {
    stream.emit('session', { ...state, status: 'working', turns: 1, worker_active: true, queued: 0 });
    batch.forEach(event => stream.emit('run_event', event));
  });
  await act(async () => { resolve(Response.json(detail)); });  // loaded after the stream said working: still working
  expect(await screen.findByText('Working')).toBeVisible();
  act(() => {  // the stream reconnects and replays from an earlier id
    stream.dispatchEvent(new Event('error'));
  });
  expect(await screen.findByText('Reconnecting…')).toBeVisible();
  act(() => {
    [...batch, message(4)].forEach(event => stream.emit('run_event', event));
  });
  const activity = within(screen.getByRole('region', { name: 'Agent activity' }));
  expect(await activity.findByText('Message 4')).toBeVisible();
  for (const id of [1, 2, 3, 4]) expect(activity.getAllByText(`Message ${id}`)).toHaveLength(1);
  expect(screen.queryByText('Reconnecting…')).toBeNull();
});

test('opening shows the latest page, streams after its newest event and loads earlier events near the top', async () => {
  const urls: string[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    urls.push(url);
    if (url.endsWith('/events/history?before=150&limit=100')) return page([message(120)]);
    if (url.endsWith('/events/history?limit=100')) return page([message(150), message(151)], true);
    return Response.json({ ...detail, status: 'waiting', turns: 1, queued: 0 });
  }));
  render(<MemoryRouter><ClaudeCodeSessionView sessionId="s1" /></MemoryRouter>);
  expect(await screen.findByText('Message 151')).toBeVisible();
  const stream = await opened();
  expect(stream.url).toBe('/api/claude-code/sessions/s1/events?after=151');  // the stream continues after the newest
  expect(screen.queryByText('Beginning of the session')).toBeNull();
  const panel = screen.getByTestId('activity-scroll');
  Object.defineProperty(panel, 'scrollHeight', { configurable: true, get: () => panel.querySelectorAll('[data-event]').length * 100 });
  Object.defineProperty(panel, 'clientHeight', { configurable: true, value: 50 });
  Object.defineProperty(panel, 'scrollTop', { configurable: true, writable: true, value: 0 });
  fireEvent.scroll(panel);  // near the top, away from the bottom
  expect(await screen.findByText('Message 120')).toBeVisible();
  expect(screen.getByText('Beginning of the session')).toBeVisible();
  expect(panel.scrollTop).toBe(100);  // the earlier event went above: what was on screen stays in place
  const history = () => urls.filter(url => url.includes('/events/history'));
  expect(history()).toEqual(['/api/claude-code/sessions/s1/events/history?limit=100',
    '/api/claude-code/sessions/s1/events/history?before=150&limit=100']);
  panel.scrollTop = 0;
  fireEvent.scroll(panel);  // the beginning is reached: nothing more to load
  expect(history()).toHaveLength(2);
  fireEvent.click(screen.getByRole('button', { name: 'Jump to latest' }));
  expect(panel.scrollTop).toBe(300);
  expect(screen.queryByRole('button', { name: 'Jump to latest' })).toBeNull();
  act(() => stream.emit('run_event', message(152)));
  expect(await screen.findByText('Message 152')).toBeVisible();
  await waitFor(() => expect(panel.scrollTop).toBe(400));  // following the latest events again
});

test('the session page has one page heading, the session title', async () => {
  const { default: AdminLayout } = await import('../AdminLayout');
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.includes('/events/history') ? page([])
    : url.startsWith('/api/claude-code/') ? Response.json(detail) : Response.json({ is_admin: true })));
  render(<MemoryRouter initialEntries={['/documentation/generations/s1']}><AdminLayout><ClaudeCodeSessionView sessionId="s1" /></AdminLayout></MemoryRouter>);
  expect(await screen.findByRole('heading', { name: 'Payments – API reference', level: 1 })).toBeVisible();
  expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
  expect(screen.getByRole('link', { name: 'Generate documents' })).toHaveAttribute('href', '/documentation/generations');
});
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-ui && pnpm exec vitest run app/components/__tests__/claudeCodeSession.test.tsx`
Expected: FAIL: the stream opens at `/events` without `after`, and there is no `activity-scroll` panel.

- [ ] **Step 3: Implement**

`services/admin-ui/app/lib/claudeCode.ts`: replace `sessionEventsUrl` with:

```ts
/** Events per page; a session page opens with the latest page and loads earlier pages as you scroll up. */
export const HISTORY_PAGE = 100;
/** The live stream after an event id (0: from the beginning); on a reconnect the browser resumes from the last id. */
export const sessionEventsUrl = (id: string, after = 0) => `${session(id)}/events?after=${after}`;
export const getSessionHistory = (id: string, before?: number, signal?: AbortSignal) =>
  request<{ events: SessionEvent[]; has_more: boolean }>(`${session(id)}/events/history?${before ? `before=${before}&` : ''}limit=${HISTORY_PAGE}`, { signal });
```

Replace `services/admin-ui/app/components/ClaudeCodeSessionView.tsx` with (`EventLine` is today's, unchanged):

```tsx
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Link } from 'react-router';
import { ApiError } from '~/lib/api';
import { closeSession, formatTokens, getSession, getSessionHistory, saveSessionDocument, sendMessage, sessionEventsUrl, statusLabels,
  stopSession, type SavedDocument, type SessionDetail, type SessionEvent, type SessionState } from '~/lib/claudeCode';
import { SAME_DOCUMENT_NAME } from '~/lib/documents';
import Markdown from './Markdown';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle } from '~/components/ui/alert-dialog';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { Field, FieldLabel } from '~/components/ui/field';
import { Skeleton } from '~/components/ui/skeleton';
import { Textarea } from '~/components/ui/textarea';

function EventLine({ event }: { event: SessionEvent }) {
  const payload = event.payload as Record<string, unknown>;
  const text = (key: string) => String(payload[key] ?? '');
  switch (event.kind) {
    case 'input': return <div className="rounded-md bg-muted p-3"><p className="text-xs font-medium text-muted-foreground">You · turn {event.turn}</p>
      <p className="line-clamp-6 whitespace-pre-wrap text-sm">{text('task')}</p></div>;
    case 'message': return <p className="whitespace-pre-wrap text-sm">{text('text')}</p>;
    case 'reasoning': return <p className="whitespace-pre-wrap text-sm italic text-muted-foreground">{text('text')}</p>;
    case 'tool_call': return <div className="text-sm"><Badge variant="outline" className="mr-2">{text('name')}</Badge><code className="break-all">{text('summary')}</code></div>;
    case 'tool_result': return payload.preview ? <pre className={`max-h-48 overflow-auto rounded-md border p-2 text-xs${payload.is_error ? ' text-destructive' : ''}`}>{text('preview')}</pre> : null;
    case 'error': return <p className="text-sm text-destructive">{text('message')}</p>;
    case 'end': return <p className="text-xs text-muted-foreground">Turn {event.turn} {text('status')}</p>;
    default: return null;
  }
}

const STOPPING = 'Stopping the current turn…';
const NEAR_TOP = 80;  // px: scrolling this close to the top of Activity loads the previous page
const NEAR_BOTTOM = 40;  // px: this close to the bottom, Activity follows new events

/** One generation: the agent's activity (the latest page, earlier pages as you scroll up, live events after it),
 * messages, Stop, the draft, Save document and Close. The page fits the screen; Activity and Draft scroll on their own. */
export default function ClaudeCodeSessionView({ sessionId }: { sessionId: string }) {
  const [session, setSession] = useState<SessionDetail | null>(null);
  const [events, setEvents] = useState<SessionEvent[]>([]);
  const [older, setOlder] = useState<'more' | 'loading' | 'start'>('more');
  const [following, setFollowing] = useState(true);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [saved, setSaved] = useState<SavedDocument | null>(null);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [reconnecting, setReconnecting] = useState(false);
  const activity = useRef<HTMLDivElement>(null);
  const anchor = useRef<number | null>(null);  // Activity's scroll height before an earlier page was added above
  const status = session?.status;
  const previousStatus = useRef(status);
  useEffect(() => {  // a new status answers the Stop request
    if (previousStatus.current !== status) setNotice(current => current === STOPPING ? '' : current);
    previousStatus.current = status;
  }, [status]);

  useEffect(() => {
    const controller = new AbortController();
    setSession(null); setEvents([]); setError(''); setReconnecting(false); setOlder('more'); setFollowing(true);
    let streamed: SessionState | null = null;  // newer than a page load that finishes after it
    getSession(sessionId, controller.signal).then(detail => setSession(streamed ? { ...detail, ...streamed } : detail))
      .catch(e => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Could not open the session'); });
    let stream: EventSource | null = null;
    let lastId = 0, frame = 0;
    let pending: SessionEvent[] = [];
    const flush = () => { frame = 0; const batch = pending; pending = []; setEvents(current => [...current, ...batch]); };
    // The latest page first, then the live stream after its newest event: nothing is shown twice or skipped. Event ids
    // increase, so anything at or below the last one is a replay after a reconnect. New events are appended once per
    // animation frame, so a burst renders in a few frames.
    getSessionHistory(sessionId, undefined, controller.signal).then(page => {
      if (controller.signal.aborted) return;
      lastId = page.events.length ? page.events[page.events.length - 1].id : 0;
      setEvents(page.events);
      setOlder(page.has_more ? 'more' : 'start');
      // Leaving the page closes only this stream; the session keeps running.
      const source = new EventSource(sessionEventsUrl(sessionId, lastId));
      stream = source;
      source.addEventListener('run_event', e => {
        setReconnecting(false);
        const event = JSON.parse((e as MessageEvent).data) as SessionEvent;
        if (event.id <= lastId) return;
        lastId = event.id;
        pending.push(event);
        if (!frame) frame = requestAnimationFrame(flush);
      });
      source.addEventListener('session', e => {
        setReconnecting(false);
        const next = JSON.parse((e as MessageEvent).data) as SessionState;
        streamed = next;
        setSession(current => current && { ...current, ...next });
      });
      source.addEventListener('open', () => setReconnecting(false));
      source.addEventListener('error', () => { if (source.readyState !== 2) setReconnecting(true); });
      source.addEventListener('closed', () => { setReconnecting(false); source.close(); });
      source.addEventListener('unavailable', () => setError('The session stream is unavailable. Reload the page.'));
    }).catch(e => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Could not load the session activity'); });
    return () => { controller.abort(); stream?.close(); if (frame) cancelAnimationFrame(frame); };
  }, [sessionId]);

  useLayoutEffect(() => {
    const panel = activity.current;
    if (!panel) return;
    if (anchor.current !== null) {  // an earlier page went above: keep what the reader was looking at in place
      panel.scrollTop += panel.scrollHeight - anchor.current;
      anchor.current = null;
    } else if (following) {
      panel.scrollTop = panel.scrollHeight;
    }
  }, [events, following]);

  async function loadOlder() {
    const first = events[0];
    if (!first || older !== 'more') return;
    setOlder('loading');
    try {
      const page = await getSessionHistory(sessionId, first.id);
      anchor.current = activity.current?.scrollHeight ?? null;
      setEvents(current => [...page.events.filter(event => event.id < (current[0]?.id ?? Infinity)), ...current]);
      setOlder(page.has_more ? 'more' : 'start');
    } catch (e) {
      setOlder('more');
      setError(e instanceof Error ? e.message : 'Could not load earlier activity');
    }
  }
  function scrolled() {
    const panel = activity.current;
    if (!panel) return;
    setFollowing(panel.scrollHeight - panel.scrollTop - panel.clientHeight <= NEAR_BOTTOM);
    if (panel.scrollTop <= NEAR_TOP) void loadOlder();
  }
  function jump() {
    setFollowing(true);
    const panel = activity.current;
    if (panel) panel.scrollTop = panel.scrollHeight;
  }

  async function run(action: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('');
    try { await action(); } catch (e) { setError(e instanceof Error ? e.message : 'The request failed'); } finally { setBusy(false); }
  }
  const send = () => run(async () => {
    const result = await sendMessage(sessionId, message.trim());
    setMessage('');
    setNotice(result.working ? 'Queued. The agent gets your message when it finishes its current response.' : 'Sent. The agent starts a new turn with your message.');
  });
  const stop = () => run(async () => { await stopSession(sessionId); setNotice(STOPPING); });
  const close = () => run(async () => { await closeSession(sessionId); setSession(current => current && { ...current, status: 'closed' }); });
  async function save(updateExisting: boolean) {
    setBusy(true); setError(''); setNotice('');
    try {
      const result = await saveSessionDocument(sessionId, updateExisting);
      setConfirming(false); setSaved(result);
      setSession(current => current && { ...current, document_id: result.id });
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && e.message === SAME_DOCUMENT_NAME && !updateExisting) setConfirming(true);
      else { setConfirming(false); setError(e instanceof Error ? e.message : 'Could not save the document'); }
    } finally { setBusy(false); }
  }

  if (!session) return error ? <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert> : <Skeleton className="h-60" />;
  const closed = session.status === 'closed';
  // Header 4rem + main padding 3rem: the page never grows past the screen, so the message box stays visible.
  return <div className="flex h-[calc(100dvh-7rem)] min-h-[36rem] flex-col gap-3">
    <div className="flex shrink-0 flex-col gap-2">
      <Link className="underline" to="/documentation/generations">Generate documents</Link>
      <div className="flex flex-wrap items-center gap-3"><h1 className="text-2xl font-semibold">{session.title}</h1>
        <Badge variant={session.status === 'failed' ? 'destructive' : 'secondary'}>{statusLabels[session.status]}</Badge></div>
      <p className="text-sm text-muted-foreground">{session.agent_name} · {session.model_name} · {session.folder}/</p>
      <p className="text-sm">{`In ${formatTokens(session.input_tokens)} · Out ${formatTokens(session.output_tokens)} · Cached ${formatTokens(session.cached_tokens)}`}</p>
      {session.status === 'starting' && !session.worker_active && <Alert><AlertDescription>Waiting for the Claude Code worker. The session starts when the worker is running.</AlertDescription></Alert>}
      {session.status_message && ['failed', 'stopped'].includes(session.status) && <Alert variant={session.status === 'failed' ? 'destructive' : 'default'}><AlertDescription>{session.status_message}</AlertDescription></Alert>}
      {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
      {reconnecting && !closed && <Alert><AlertDescription role="status">Reconnecting…</AlertDescription></Alert>}
      {notice && <Alert><AlertDescription role="status">{notice}</AlertDescription></Alert>}
      {saved && <Alert><AlertDescription>Saved as version {saved.revision} in {saved.folder}. <Link className="underline" to={`/documents/${saved.id}`}>Open document</Link>{saved.export_error ? ` ${saved.export_error}` : ''}</AlertDescription></Alert>}
    </div>
    <div className="grid min-h-0 flex-1 grid-rows-2 gap-3 lg:grid-cols-2 lg:grid-rows-1">
      <section aria-label="Agent activity" className="flex min-h-0 min-w-0 flex-col rounded-lg border">
        <h2 className="shrink-0 border-b px-4 py-2 font-semibold">Activity</h2>
        <div ref={activity} data-testid="activity-scroll" onScroll={scrolled} className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-4">
          {older === 'start' && <p className="text-center text-xs text-muted-foreground">Beginning of the session</p>}
          {older === 'loading' && <p role="status" className="text-center text-xs text-muted-foreground">Loading earlier activity…</p>}
          {events.length === 0 && <p className="text-sm text-muted-foreground">The agent's messages, thinking and tool calls appear here.</p>}
          {events.map(event => <div key={event.id} data-event><EventLine event={event} /></div>)}
          {session.queued > 0 && <p className="text-sm text-muted-foreground">{session.queued} message{session.queued === 1 ? '' : 's'} queued</p>}
        </div>
        {!following && <div className="shrink-0 border-t p-2 text-center"><Button type="button" size="sm" variant="secondary" onClick={jump}>Jump to latest</Button></div>}
      </section>
      <section aria-label="Draft" className="flex min-h-0 min-w-0 flex-col rounded-lg border">
        <h2 className="shrink-0 border-b px-4 py-2 font-semibold">Draft{session.draft_turn ? ` · turn ${session.draft_turn}` : ''}</h2>
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {session.draft ? <Markdown content={session.draft} /> : <p className="text-sm text-muted-foreground">The draft appears when the agent finishes a turn.</p>}
        </div>
      </section>
    </div>
    <form className="flex shrink-0 flex-col gap-2" onSubmit={e => { e.preventDefault(); void send(); }}>
      <Field><FieldLabel htmlFor="session-message">Message</FieldLabel><Textarea id="session-message" rows={2} maxLength={20000} disabled={closed} value={message} onChange={e => setMessage(e.target.value)} placeholder="For example: shorten the API section" /></Field>
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={busy || closed || !message.trim()}>Send message</Button>
        <Button type="button" variant="outline" disabled={busy || session.status !== 'working'} onClick={() => void stop()}>Stop</Button>
        <Button type="button" variant="outline" disabled={busy || !session.draft} onClick={() => void save(false)}>Save document</Button>
        <Button type="button" variant="outline" disabled={busy || closed} onClick={() => void close()}>Close session</Button>
      </div>
    </form>
    <AlertDialog open={confirming} onOpenChange={setConfirming}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>Save a new version?</AlertDialogTitle>
      <AlertDialogDescription>{`${session.folder} already has a document named “${session.document_name}”. Saving adds a new version of it; earlier versions stay available.`}</AlertDialogDescription></AlertDialogHeader>
      <AlertDialogFooter><AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel><AlertDialogAction disabled={busy} onClick={e => { e.preventDefault(); void save(true); }}>Save new version</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
  </div>;
}
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd services/admin-ui && pnpm exec tsc --noEmit && pnpm exec vitest run app/components/__tests__/claudeCodeSession.test.tsx app/components/__tests__/claudeCodeSessions.test.tsx app/components/__tests__/claudeCodeSessionForm.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-ui/app/lib/claudeCode.ts services/admin-ui/app/components/ClaudeCodeSessionView.tsx \
  services/admin-ui/app/components/__tests__/claudeCodeSession.test.tsx
git commit -m "feat(admin-ui): the session page fits the screen, follows new events and loads earlier ones on scroll

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Docs and full verification

**Files:**
- Modify: `docs/user-guide.md`
- Modify: `docs/documentation.md`
- Modify: `docs/code-processing.md`
- Delete: `docs/documentation-updates.md`
- Modify: `docs/database.md`
- Modify: `docs/architecture.md`
- Modify: `docs/agent-profiles.md`
- Modify: `deploy/README.md`
- Modify: `services/admin-api/src/knowledge_admin/tools/README.md`

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write the docs**

`docs/user-guide.md`:
- **Contents:** `4.13 Documentation generation` becomes `4.13 Generate documents` (anchor `#413-generate-documents`).
- **§1 Architecture at a glance:** remove `· documentation-worker (doc generation)`; the `claude-code-worker` line
  becomes `claude-code-worker (Generate documents sessions)`.
- **§3 step 8:** `**Docs → Generate documents** → **New**: choose a Claude Code agent and what to document → **Generate**.`
- **§4.1 Navigation:** the Docs row becomes `Documents · Prompts · Generate documents · Folders`; the LLM Settings
  sentence drops "documentation generation".
- **§4.11 Agents:** the **Test** row for Claude Code becomes "**Start a session** opens **Docs → Generate documents →
  New** with this agent".
- **§4.13 Generate documents:** replace the whole section with: what it is (an agent writes one document per session;
  the list at `/documentation/generations` shows status, agent, tokens, saved document; **New** opens the form;
  sessions continue when you leave and close after 24 idle hours); prerequisites (an enabled Claude Code agent with a
  Claude model; code projects with folders, or environments/services linked to code projects, or an agent with code
  projects — Claude Code needs at least one working folder); the form (Agent; What to document with Code / Environment
  / Services / Servers and their pickers, at least one item; Instructions; Document name and folder, default
  `<first code project>-docs` or `docs`, lowercase rule); "There is no model or prompt field: the agent runs with its own
  model, prompt, instructions, skills and Claude Code tools. Put a documentation prompt in the agent (Docs → Prompts holds
  the earlier prompts to copy from)."; what the agent receives (its system prompt, then the first message — include the
  example from the spec with the `code: … (<path>)` form and the final-answer sentence); working folders (chosen code
  projects, the code projects linked to chosen environments and services, then the agent's own code projects; read-only;
  the first is the working directory); servers are text only (no SSH from this form; SSH through the agent comes with
  the tool gateway); the session page (Activity and Draft side by side with their own scroll, the message box always
  visible, Activity follows new events at the bottom, **Jump to latest** after you scroll up, the latest 100 events
  first and earlier pages as you scroll up until **Beginning of the session**; Send message, Stop, Save document with
  the same-name confirmation, Close session); upgrade note (migration 0073 removed the service, whole-environment and
  code-project generation, its workflows, history and run records; saved documents, their versions and exported files
  stay, and documents written by service phases keep exporting into `services/<service>/<phase>/`; old
  `/documentation/sessions` links redirect).
- **§4.14 Documents and documentation prompts:** remove **View phases / regenerate** and the **Sessions** paragraph
  (`/documentation/sessions`); say documentation prompts are a library: nothing generates with them directly; copy one
  into a documentation agent's prompt.
- **§4.16 Agent runs:** the run type filter offers `test`, `board` and `claude_code`.
- **§5:** **B** becomes: link the code project to a service → create a Claude Code agent with a documentation prompt →
  **Generate documents → New**, **Services**: the service → **Generate** → follow, message, **Save document**; **C**
  becomes: **Environment**: the environment (or **Servers**) with instructions about deployments → **Generate**, noting
  that servers are described as text until agents get SSH tools; **G** step 1 becomes **Generate documents → New** →
  **Code**: the project → **Generate** → **Save document**; **H** step 2 becomes **Docs → Generate documents → New** (or
  the agent's **Test** tab → **Start a session**) → choose what to document → **Generate**.
- **§7 Current limitations:** add "Generate documents runs Claude Code agents only (native agents come with the session
  API) and describes servers as text (no SSH from the form)".

`docs/documentation.md`: the intro lists Documents, Prompts, Generate documents and Folders; replace **Migration and
next stage** and **Documentation workflows** with a **Generate documents** section: `POST /api/claude-code/sessions`
body (the eight fields), the refusals (`NOTHING_CHOSEN`, `NO_FOLDER`, `AGENT_MODEL`, unknown ids), the context snapshot
and prompt snapshot keys, the first message format, `GET /api/claude-code/sessions/{id}/events/history`
(`before`, `limit`, `has_more`, redaction), and that migration 0073 dropped `documentation_generations`,
`documentation_workflows`, `documentation_workflow_jobs`, the `documentation_generate` job kind and `doc:*` run records,
and added `documents.phase_metadata` (read by `export_input`).

`docs/code-processing.md`: retitle to `# Code processing`; keep **Phase 1: Generate and inspect the graph** (renamed
**Generate and inspect the graph**), **Limits of the graph** and **Development verification**; delete **Saved
workflows**, **Phase 2**, **Phase 3**, **Phase 4** and **Additional documentation**; the intro says graphs are generated
from **Code → Code graphs** and read by agents (code graph tools come with the tool catalog).

`git rm docs/documentation-updates.md` (its module was removed in Task 1); remove links to it (`git grep -n
"documentation-updates" -- docs README.md`).

`docs/database.md`: remove the `documentation_generations` row and the two ER lines
(`jobs ||--o| documentation_generations`, `documentation_generations ||--o{ document_versions`); add
`documents.phase_metadata` (service phase export layout) and a note that `document_versions.generation_id` is history
without a foreign key since 0073.

`docs/architecture.md` (line 116): "Interactive documentation sessions run in `claude-code-worker`" becomes "Generate
documents sessions run in `claude-code-worker`"; remove any `documentation-worker` mention.

`docs/agent-profiles.md`: in **Persistence and APIs**, after the 0072 paragraph, add: migration
`0073_remove_documentation_generation` removes the one-shot generation (tables, job kind, history and `doc:*` run
records), adds `documents.phase_metadata`, and its down migration restores the empty schema only; the Claude Code Test
tab links to `/documentation/generations/new?agent=<id>`.

`deploy/README.md`:
- line 30: `claude-code-worker` runs **Generate documents** sessions with the Claude Agent SDK;
- the `CODE_INDEX_FOLDER` and `GENERATED_DOCS_FOLDER` rows: remove `documentation-worker` from the writers;
- add a section **Upgrading to Generate documents** after **Upgrading after the Reader removal**: run the migration
  (0073 deletes the one-shot generation's workflows, jobs, job events and run records; documents, versions and exports
  stay); remove the orphaned `documentation-worker` container with
  `docker compose --env-file .env -f deploy/compose.yaml up -d --remove-orphans`; documentation agents need a Claude
  model and a documentation prompt of their own.

`services/admin-api/src/knowledge_admin/tools/README.md` (line 172): "`AGENT_WORKSPACE_ROOT=/work/agent-workspaces` on
the API and documentation worker" becomes "on the API and the shell worker".

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

Run: `git grep -ln "documentation_generation\|documentation_workflow\|documentation-worker\|knowledge-doc-worker\|api_evidence\|documentation_updates\|DocumentationWorkflowView\|/documentation/sessions\|scope=agent" -- services deploy tests`
Expected exactly: `deploy/README.md` (the upgrade section), `services/admin-api/tests/test_documentation_fixture.py`
and `services/admin-api/tests/test_environments.py` (they run the 0073 down and older downs),
`services/admin-api/tests/test_generate_documents.py` (it checks the modules are gone),
`services/admin-api/tests/test_generate_documents_migration.py`, `services/admin-ui/app/components/__tests__/generateDocumentsRoutes.test.tsx`,
`services/admin-ui/app/routes.ts` (the redirect routes) and `tests/test_setup.py` (the retired service check).

Run: `git grep -n "documentation-worker\|New documentation\|With an agent\|Whole environment\|documentation workflow\|Sessions\*\*" -- docs/user-guide.md docs/architecture.md docs/documentation.md docs/code-processing.md docs/agent-profiles.md`
Expected: no matches.

Run: `git grep -n "first_message" -- services/admin-api/src`
Expected: `generate_documents.py` (definition) and `claude_code_api.py` (import and call).

Run: `git grep -n "prompt_id\|prompt_version_id\|'notes'\|comments" -- services/admin-api/src/knowledge_admin/claude_code_api.py services/admin-ui/app/components/ClaudeCodeSessionForm.tsx services/admin-ui/app/lib/claudeCode.ts`
Expected: no matches.

- [ ] **Step 4: Commit**

```bash
git add -A docs deploy/README.md services/admin-api/src/knowledge_admin/tools/README.md
git commit -m "docs: Generate documents runs an agent; the one-shot generation is gone (knowledge agents step 4)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

## Follow-ups (out of scope for this plan)

- **Step 2 — tool catalog and gateway:** Claude Code agents then reach servers through their own SSH configuration;
  the Generate documents form keeps describing servers as text.
- **Step 3 — native agents through the session API:** the form offers native agents too (no working folder needed),
  `/api/claude-code/sessions*` moves to `/api/agent-sessions*`, and the `ClaudeCodeSession*` components are renamed.
- **Docs → Prompts:** decide whether the documentation prompt library stays, becomes agent prompt templates, or goes.
