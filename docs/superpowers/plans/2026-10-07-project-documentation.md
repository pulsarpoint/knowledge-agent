# Code Project Documentation Into Named Folders Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An administrator generates one document for one code project with a chosen
documentation prompt and per-run comments, and saves it into a named documentation folder
under `GENERATED_DOCS_FOLDER` (`<folder>/<document-slug>/README.md`); the default folder is
the project's slug plus `-docs`. PostgreSQL stays the source of truth with versions. A new
**Docs → Folders** page browses those folders, their documents and, read-only, the RAG
collections that include them, and links to **RAG → Collections**. There, **New
collection** can now select folders: creating it connects them and queues a sync at once.
Nothing is copied, and the RAG service is unchanged.

**Architecture:**
- **Rules (`knowledge_admin/data_root.py`, `knowledge_admin/documents.py`)**:
  `documentation_folder()` validates a folder with RAG's segment rule (plus no
  `node_modules` segment and no first segment `services`), `generated_docs_folder()` gives
  `GENERATED_DOCS_FOLDER` relative to `DATA_ROOT` (from `DOCUMENT_OUTPUT_ROOT`), and
  `document_slug()` turns a document name into its slug.
- **Migration 0068** adds the nullable `ka.documents.folder` (checked with the same rule),
  replaces `UNIQUE (project_id, slug)` with `UNIQUE NULLS NOT DISTINCT (project_id, folder,
  slug)` and rebuilds `ka.document_details` (it expands `d.*`).
- **Documents (`documents.py`)**: `save_document(..., folder=)` places a new document in a
  folder; an existing document keeps its folder (the editor never sends one). Exports go
  to `<root>/<folder>/<slug>/` (`export_location`, `export_document`, `export_input`,
  `remove_exported_folder`, `remove_export`, `backfill_exports`). `export_conflict()`
  refuses a folder whose first segment is a folderless document's export folder, and the
  reverse.
- **Generation (`documentation_generation.py`)**: `GenerationInput` gains
  `scope='code_project'` with `code_project_id`, `prompt_id`, `prompt_version_id`,
  `comments`, `document_name`, `folder`, `update_existing`. `queue_code_project()` queues
  a standalone `documentation_generate` job (no workflow) whose agent snapshot
  `project` has code tools on and SSH, RAG documents, MCP, skills and services off; its
  system prompt is the active prompt plus the comments. The worker runs target kind
  `project` through the existing loop, saves a `purpose='code'` document linked to the
  code project in its folder, and exports it there. The generation history gains a
  `scope` filter and `scope`, `document_name`, `folder` fields.
- **Folders API (`documentation_folders.py`, new, read-only)**: `GET /api/documentation/folders`
  (folders with document count, code projects, last generated and their RAG folder path)
  and `GET /api/documentation/folders/{folder}` (its documents). They never call RAG.
- **admin-ui, documentation**: **New documentation** gets a third scope, **Code project**,
  which renders `CodeProjectDocumentationForm` (Phase 1 graph gating with the existing
  `CodeGraphPhase`, code/mixed prompts only, comments, document name, folder defaulting to
  `<project-slug>-docs`, same-name confirmation). The Generate documents page shows the
  code project runs. A new **Docs → Folders** page (`DocumentationFoldersView`) browses the
  folders and their documents and shows, read-only, the collections whose folders include
  each one (from `GET /api/rag/collections?status=false`), with a link **Index in RAG →
  Collections** to `/rag/collections?folder=generated-documents/<folder>`.
- **admin-ui, RAG**: **New collection** (`RagCollectionForm`) gets an optional **Folders**
  selection built on the existing folder picker (`FolderBrowser`, extracted from
  `RagFolderPicker`). **Create collection** chains RAG's existing calls: `POST collections`,
  `POST collections/{id}/paths` per folder, `POST collections/{id}/sync`. If a folder
  cannot be added, the collection is kept and opened with the error. `?folder=` opens New
  collection with that folder selected.

**Tech Stack:**
- PostgreSQL 17 with pgvector, golang-migrate SQL migrations, `db/tests/run.sh`;
- FastAPI, pydantic v2 and psycopg3 (`services/admin-api`), pytest against a real
  PostgreSQL container (`knowledge-agent-v3-postgres:17.10-vector0.8.6`), `httpx.MockTransport`
  as the fake RAG;
- React Router 7 with shadcn/ui, Vitest with Testing Library and user-event (`services/admin-ui`).

**Spec:** `docs/superpowers/specs/2026-10-07-project-documentation-design.md` (binding).

## Global Constraints

- Implementers and reviewers never run make setup/compose-validate/up/smoke and never touch .env or .local; compose checks use a temp .env.
- Folder rule (one function, `knowledge_admin.data_root.documentation_folder`; the UI mirrors it as `folderError`): `/`-separated segments each matching `[A-Za-z0-9_-][A-Za-z0-9._-]*` (so no leading `/`, no `..`, no hidden or empty segments), at most 200 characters, no `node_modules` segment, first segment not `services` (the service phase exports live in `<GENERATED_DOCS_FOLDER>/services/`).
- Folder rule text (400 responses, `ValueError`, UI): `A documentation folder is a path such as payments or team/payments: each segment uses letters, digits, '.', '_' and '-' and does not start with '.'; node_modules and a first segment services are not allowed; at most 200 characters.`
- DB check `documents_folder_check`: `length(folder) <= 200 AND folder ~ '^[A-Za-z0-9_-][A-Za-z0-9._-]*(/[A-Za-z0-9_-][A-Za-z0-9._-]*)*$' AND folder !~ '(^|/)node_modules(/|$)' AND split_part(folder, '/', 1) <> 'services'`.
- Slug from the document name: `re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:120].rstrip('-')`; an empty slug is refused (`Enter a document name with letters or digits`). `Payments – API reference` → `payments-api-reference`.
- Default folder (UI only, always editable; pick an existing folder or type a new one): the project name as a slug plus `-docs` — `base = name.toLowerCase().replace(/[^a-z0-9._-]+/g, '-').replace(/^[-.]+|-+$/g, '').slice(0, 195).replace(/-+$/, '')`, then `base ? `${base}-docs` : 'docs'`; `Payments API` → `payments-api-docs`. The folder rule still applies to whatever is submitted.
- Export path: `<DOCUMENT_OUTPUT_ROOT>/<folder>/<slug>/README.md`, plus `architecture-<n>.d2` per D2 block and `metadata.json` (`{"source_commits": …}`) as today. A document without a folder exports to `<DOCUMENT_OUTPUT_ROOT>/<slug>/README.md` as today.
- RAG folder of a documentation folder: `<generated_docs_folder()>/<folder>`, where `generated_docs_folder()` is `DOCUMENT_OUTPUT_ROOT` relative to `DATA_ROOT_DIR` (default `generated-documents`); outside `DATA_ROOT_DIR` → `ValueError('DOCUMENT_OUTPUT_ROOT must be a folder inside DATA_ROOT_DIR')`.
- Slug uniqueness: constraint `documents_project_id_folder_slug_key UNIQUE NULLS NOT DISTINCT (project_id, folder, slug)` replaces `documents_project_id_slug_key`.
- Top-level export collision: a folder whose first segment equals a folderless document's slug → 409 `The folder <first> holds the export of the document <first>. Choose another folder.`; a folderless slug equal to an existing folder's first segment → 409 `The slug <slug> is used by a documentation folder. Choose another slug.`
- Generation input for `scope='code_project'`: `code_project_id`, `prompt_id`, `prompt_version_id` required; `comments` ≤ 20,000 characters, no NUL; `document_name` ≤ 240 characters, trimmed, no NUL; `folder` (folder rule); `update_existing` (default `false`); `code_graph_hashes`, `source_commits` and `model_id` as today. `units`, `workflow_id`, `service_id`, `environment_id`, `code_project_ids`, `server_ids` and `instructions` must be empty. Service and environment scopes refuse every code-project field and still need at least one unit.
- Generation errors: server-only prompt → 400 `Select an active documentation prompt for code (code or mixed input)`; prompt changed → 409 `The prompt changed. Reload the prompt before generating.`; same name in the same folder without confirming → 409 `A document with this name already exists in this folder. Confirm to save a new version.` (`SAME_DOCUMENT_NAME`, also in `lib/documents.ts`); a queued or running run for the same code project or the same folder and slug → 409 `Documentation for this code project or document is already queued or running`; no or stale graph → 409 with `load_code_graph`'s message.
- Agent snapshot key `project`: `id: 'documentation-project'`, `name: 'Documentation: code project'`, `system_prompt.content` = active prompt content + `\n\n## Comments for this run\n\n<comments>` when the comments are not blank, `code.enabled: true` with the one project and its graph, `ssh`, `documents`, `mcp`, `skills`, `services` all disabled. Target kind `project`; prompt snapshot kind `project` (with `comments`); generation `instructions` = the comments; `mode` = `update` when confirmed, else `create`. Saved document: `purpose='code'`, `title` = the document name, `code_project_ids` = `[code_project_id]`, `source_commits` = the run's commits, no services, servers, deployments or environments.
- Folders API (read-only, never calls RAG): `GET /api/documentation/folders` → `{generated_folder, folders: [{folder, document_count, updated_at, last_generated_at, code_projects, source_path}]}` (`generated_folder`/`source_path` are `null` when `DOCUMENT_OUTPUT_ROOT` is outside `DATA_ROOT_DIR`; `code_projects` are distinct names, sorted). `GET /api/documentation/folders/{folder}` → `{folder, documents: [{id, slug, title, version, updated_at, last_generated_at, code_projects}]}` or 404 `Documentation folder not found`. A bad folder → 400 with the rule text.
- Folders page RAG state: from `GET /api/rag/collections?status=false`; a collection includes a folder when one of its paths equals `source_path` or is a parent of it (`source_path.startsWith(path + '/')`, shown as `through <path>`); none → **Not indexed**. Each collection links to `/rag/collections/<id>`; each folder links **Index in RAG → Collections** to `/rag/collections?folder=<encodeURIComponent(source_path)>`. No index or disconnect actions on the Docs side.
- RAG **New collection** with folders: selected folders are normalized (`normalizeFolder`) and unique; **Create collection** sends `POST /api/rag/collections` (settings as today), then `POST /api/rag/collections/<id>/paths {source_path}` per folder in selection order, then `POST /api/rag/collections/<id>/sync {force: false}`; with no folders only the first call is made (as today). A failed folder or sync stops the chain, keeps the collection, and opens `/rag/collections/<id>` with `state.folderError` = `Collection <name> was created, but folder <path> could not be added: <RAG message>. Fix its folders here.` (or `… but its sync could not be queued: …`), shown as a destructive alert on the collection page. `?folder=<path>` opens New collection with `[normalizeFolder(path)]` selected and the name `collectionNameFor(path)` (last segment, lowercase, runs of other characters → `-`, ≤ 60).
- Generation history `GET /api/documentation/generations` gains `scope` (`service` | `environment` | `code_project`) and the fields `scope`, `document_name`, `folder`.
- Migration number 0068 (`0068_document_folders`). `db/tests/run.sh` pins the rolled-back version to `67`. The down migration raises `Documents in different folders share a slug; rename or delete them before rolling back 0068` when two documents of a project share a slug.
- UI: sidebar **Docs** group order Documents · Prompts · Generate documents · Folders (`/documentation/folders`, icon `FolderOpen`); folder pages `/documentation/folders/<folder>`.
- The RAG service and its API do not change; admin-api gains no RAG calls.
- Commit trailer: `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Python stays compatible with 3.12.

## Review Focus

1. **Folder names that escape or collide with RAG's rules.** Examples: `../payments`,
   `.hidden`, `team/.git`, `a//b`, `/srv/docs`, `my docs`, `team/node_modules`,
   `services/payments`, 201 characters, and `a%20b` in a route URL. Each is refused with
   the rule text before anything is queued, written or sent to RAG; the database refuses
   them too. Pinned in:
   - Task 1: `test_documentation_folders_follow_the_rag_segment_rule`;
   - Task 2: `test_folder_column_and_per_folder_slug_uniqueness`;
   - Task 4: `test_code_project_input_is_validated`;
   - Task 6: `test_folder_routes_reject_bad_names`.
2. **The wrong document gets a new version.** Examples: a service generation whose slug
   equals a folder document's slug; the same name in another folder; the same name in the
   same folder without confirming; two documents in different folders blocking a
   rollback. Only the same slug in the same folder is updated, and only when confirmed.
   Pinned in:
   - Task 4: `test_service_generation_ignores_folder_documents_with_the_same_slug`;
   - Task 5: `test_same_name_in_the_same_folder_is_refused_or_saved_as_a_new_version`;
   - Task 2: `test_down_restores_project_wide_slugs_and_refuses_shared_slugs`.
3. **Exports landing in the wrong place.** Examples: a folder document exported to the
   root; an editor save (no folder in the body) moving the document out of its folder; a
   slug rename leaving the old export; a delete leaving files; a folder `payments` next to
   a folderless document `payments` (one folder would hold both, and indexing the folder
   would pick up the other document). Pinned in Task 3:
   - `test_folder_documents_export_into_their_folder_and_keep_it_on_edit`;
   - `test_a_folder_never_shares_a_top_level_export_folder_with_a_document`.
4. **The run gets more access or another prompt than chosen.** Examples: SSH, RAG
   documents or MCP enabled; a server-only prompt; a prompt changed since the form
   loaded; comments missing from the system prompt; a graph that changed during the run
   still being published. Pinned in:
   - Task 4: `test_code_project_generation_snapshots_the_prompt_comments_and_code_tools_only`;
   - Task 5: `test_code_project_document_is_saved_linked_and_exported_into_its_folder`;
   - Task 5: `test_code_project_document_is_not_saved_when_the_graph_changes_during_the_run`.
5. **New collection with folders leaving a half-made or silent state.** Examples: a
   folder that does not exist or overlaps another selected folder; a sync queued before
   every folder was connected; the created collection deleted on failure (the admin then
   loses its settings); an error that disappears when the page changes; a `?folder=`
   prefill that is not the folder the Docs page meant; a lookalike collection path
   (`generated-documents/pay`) shown as including `generated-documents/payments`. The chain
   stops at the first failure, keeps and opens the collection with the error, and never
   syncs a partial selection. Pinned in:
   - Task 8: `New collection with folders connects each folder and queues a sync right away`;
   - Task 8: `a folder that cannot be added keeps the collection and opens it with the error`;
   - Task 8: `?folder= opens New collection with that folder selected and a name from it`;
   - Task 9: `folders show their documents, projects and the RAG collections that include them, read-only`.

## Decisions (where the spec leaves a choice)

- **Standalone generation, no workflow.** `ka.documentation_workflows` is built around one
  service or environment (a `scope` check, drafts, phase tables). A code project run is
  one form and one job, so it is a plain `ka.documentation_generations` row with
  `source_snapshot.scope = 'code_project'` and no `documentation_workflow_jobs` link.
  **New documentation → Code project** shows the form directly (no **Create workflow**);
  after **Generate** the page returns to **Docs → Generate documents**, which shows a
  second table, **Code project documents** (the existing `DocumentationGenerationsView`
  with `scope=code_project`). Its rows show `<document name> · <folder>`.
- **The model is chosen on the New documentation page**, as for the other scopes; the spec's
  form table lists only the per-run fields.
- **Its own unit kind, `project`, through the existing worker loop.** It reuses the
  usage recording, progress events, graph verification and failure mapping. The agent
  gets the same structured `GeneratedDocument` output as `code` (60 turns, 1200 s); the
  saved title is always the document name. The chosen prompt plus the comments is the
  system prompt, so the comments are appended exactly once; the task JSON carries the
  project, its graph hash, the document name and the existing document (for a new
  version).
- **Purpose `code`, linked only to the code project.** Prompt and model are recorded
  through the generation (`prompt_snapshot`, `agent_snapshot.project.model`) that
  `document_versions.generation_id` points to; `metadata.json` keeps today's
  `source_commits` content.
- **Graph-first gating is the existing Phase 1 component.** The form shows `CodeGraphPhase`
  for the selected project and enables **Generate** only when the project has a graph
  hash. A graph made stale by later source changes is caught by the server
  (`load_code_graph` → 409 "Source or graph selection changed…"), shown on the form, and
  fixed with **Generate graph**, exactly as for services today.
- **Same-name confirmation reacts to the server's 409.** The form submits without
  `update_existing`; on 409 with `SAME_DOCUMENT_NAME` it opens a dialog, and **Save new
  version** resubmits with `update_existing: true`.
- **Folders come from the database.** A folder is listed when at least one document
  records it (`ka.documents.folder`); the folder field's suggestions are the same list
  (an `<input list>` datalist), and typing a new name creates the folder on the first
  export.
- **`services` and top-level collisions are refused.** `<GENERATED_DOCS_FOLDER>/services/`
  holds the service phase exports, and a folderless document `payments` exports to
  `<GENERATED_DOCS_FOLDER>/payments/`; a folder sharing either first segment would mix
  exports, and connecting that folder to a RAG collection would index unrelated files.
- **Indexing lives in RAG → Collections, not in Docs.** The Docs side only browses and
  links. RAG's New collection already owns names and settings; adding an optional folder
  selection there serves every folder under `DATA_ROOT`, not only documentation folders,
  and needs no new admin-api or RAG route.
- **The chain runs in the admin UI** over RAG's existing calls (`createCollection`,
  `addFolder`, `syncCollection`). The sync is queued only after every folder was
  connected, so a partial selection is never processed. On failure the collection is
  kept (its settings are not lost) and opened with the error in router state; the admin
  fixes folders there with the existing Add folder, Remove folder and Refresh.
- **Folder selection reuses the existing picker.** `FolderBrowser` is the browsing list
  and **Folder path** field of `RagFolderPicker`, extracted unchanged; the Add folder sheet
  and the New collection form both use it. The browser mounts only after **Choose a
  folder**, so creating without folders never lists `DATA_ROOT`.
- **Read-only RAG state on the Folders page** comes from the UI's existing
  `getCollectionNames()` (`GET /api/rag/collections?status=false`, no folder walks): a
  collection includes a folder when a path equals it or is a parent of it (for example a
  collection that holds all of `generated-documents`). Live status (Up to date, Needs
  refresh) stays on the collection pages, which each name links to. When RAG is down the
  folders still list, with a notice.
- **The `?folder=` prefill** names the collection after the folder's last segment
  (`generated-documents/payments-api-docs` → `payments-api-docs`); both stay editable.
- **Default folder `<project-slug>-docs`**: one folder per project by default, named so
  it reads as documentation next to other folders; the admin can pick an existing folder
  or type any valid one.
- **The 0068 rollback refuses shared slugs** instead of renaming documents; dropping the
  folder column is otherwise lossless for documents (they export at the root again).

## Delivery and test boundaries

Tasks land in order on `feature/project-documentation`, and every suite is green after
each task:
- Task 1 (rules) has no schema change.
- Task 2's migration must precede Task 3: admin-api tests run every migration and Task 3
  writes `documents.folder`.
- Tasks 4–5 need Task 3 (`save_document(folder=)`, `export_conflict`, folder exports).
- Task 6 needs Tasks 1–3. Task 7 needs the shapes of Tasks 4 and 6. Task 8 (RAG New
  collection) needs no backend change. Task 9 needs Tasks 6–8 (the folder API, its types
  and the `?folder=` prefill).

Each task lists the exact tests to run; Task 10 runs every suite.

---

### Task 1: Folder, data-root and slug rules

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/data_root.py`
- Modify: `services/admin-api/src/knowledge_admin/documents.py` (add `document_slug`)
- Modify: `services/admin-api/tests/test_data_root.py`
- Modify: `services/admin-api/tests/test_documents.py`

**Interfaces:**
- Produces, in `knowledge_admin.data_root`: `FOLDER_RULE: str`,
  `documentation_folder(value: str) -> str` (raises `ValueError(FOLDER_RULE)`),
  `generated_docs_folder() -> str`.
- Produces, in `knowledge_admin.documents`: `document_slug(name: str) -> str`.

- [ ] **Step 1: Write the failing tests**

In `services/admin-api/tests/test_data_root.py`, change the import line to:

```python
from knowledge_admin.data_root import (FOLDER_RULE, data_dir, data_path, documentation_folder, generated_docs_folder,
                                       host_path, jira_folder)
```

and append:

```python
def test_documentation_folders_follow_the_rag_segment_rule():
    for good in ('payments', 'team/payments', 'v1.2_docs-x', 'A/b-c', 'team/services'):
        assert documentation_folder(good) == good
    for bad in ('', '/payments', 'payments/', 'a//b', '../payments', 'team/..', '.hidden', 'team/.git', 'my docs',
                'team/node_modules', 'node_modules', 'services', 'services/payments', 'é', 'x' * 201):
        with pytest.raises(ValueError) as error:
            documentation_folder(bad)
        assert str(error.value) == FOLDER_RULE
    assert FOLDER_RULE.startswith('A documentation folder is a path such as payments or team/payments')


def test_generated_documents_folder_is_relative_to_the_data_root(monkeypatch):
    monkeypatch.setenv('DATA_ROOT_DIR', '/work/data')
    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', '/work/data/exports/docs')
    assert generated_docs_folder() == 'exports/docs'
    monkeypatch.delenv('DOCUMENT_OUTPUT_ROOT')
    assert generated_docs_folder() == 'generated-documents'
    for outside in ('/work/other/docs', '/work/data'):
        monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', outside)
        with pytest.raises(ValueError, match='DOCUMENT_OUTPUT_ROOT must be a folder inside DATA_ROOT_DIR'):
            generated_docs_folder()
```

In `services/admin-api/tests/test_documents.py`, append:

```python
def test_document_slug_comes_from_the_name():
    from knowledge_admin.documents import document_slug
    assert document_slug('Payments – API reference') == 'payments-api-reference'
    assert document_slug('  Café v2.0 / Ops  ') == 'caf-v2-0-ops'
    assert document_slug('– –') == ''
    assert document_slug('a' * 119 + ' b') == 'a' * 119
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_data_root.py tests/test_documents.py::test_document_slug_comes_from_the_name -q`
Expected: FAIL with `ImportError: cannot import name 'FOLDER_RULE'` and
`ImportError: cannot import name 'document_slug'`.

- [ ] **Step 3: Implement**

In `services/admin-api/src/knowledge_admin/data_root.py`, add `import re` below
`import os`, and append:

```python
SEGMENT = r'[A-Za-z0-9_-][A-Za-z0-9._-]*'
FOLDER = re.compile(rf'{SEGMENT}(/{SEGMENT})*')
FOLDER_RULE = ("A documentation folder is a path such as payments or team/payments: each segment uses letters, digits, "
               "'.', '_' and '-' and does not start with '.'; node_modules and a first segment services are not allowed; "
               "at most 200 characters.")


def documentation_folder(value: str) -> str:
    """A folder under GENERATED_DOCS_FOLDER, with RAG's folder segment rule. `services` is where the service phase
    exports live, and RAG skips node_modules. Raises ValueError(FOLDER_RULE)."""
    parts = value.split('/')
    if len(value) > 200 or not FOLDER.fullmatch(value) or 'node_modules' in parts or parts[0] == 'services':
        raise ValueError(FOLDER_RULE)
    return value


def generated_docs_folder() -> str:
    """GENERATED_DOCS_FOLDER: where documents are exported, relative to DATA_ROOT (a RAG collection folder prefix)."""
    relative = _relative(os.environ.get('DOCUMENT_OUTPUT_ROOT') or f'{data_dir()}/generated-documents')
    if not relative:
        raise ValueError('DOCUMENT_OUTPUT_ROOT must be a folder inside DATA_ROOT_DIR')
    return relative
```

In `services/admin-api/src/knowledge_admin/documents.py`, add after `document_files`:

```python
def document_slug(name: str) -> str:
    """A slug from a document name: lowercase ASCII letters and digits joined by '-', at most 120 characters
    ('' when the name has none)."""
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:120].rstrip('-')
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_data_root.py tests/test_documents.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/data_root.py services/admin-api/src/knowledge_admin/documents.py \
  services/admin-api/tests/test_data_root.py services/admin-api/tests/test_documents.py
git commit -m "feat(admin-api): documentation folder rule, generated documents folder and document slugs

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Migration 0068 — `documents.folder` and slugs unique per folder

**Files:**
- Create: `db/migrations/0068_document_folders.up.sql`
- Create: `db/migrations/0068_document_folders.down.sql`
- Create: `services/admin-api/tests/test_document_folders_migration.py`
- Modify: `db/tests/run.sh` (rollback pin `66` → `67`)

**Interfaces:**
- Consumes: the 0067 schema (`ka.documents` with `documents_project_id_slug_key`,
  `ka.document_details` as created by 0064).
- Produces: `ka.documents.folder text NULL` with `documents_folder_check`;
  `documents_project_id_folder_slug_key`; `ka.document_details.folder` (through `d.*`).

- [ ] **Step 1: Write the failing test**

Create `services/admin-api/tests/test_document_folders_migration.py`:

```python
"""0068 gives documents a documentation folder with slugs unique per folder; without a folder, slugs stay unique."""
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0068_document_folders.up.sql', '0068_document_folders.down.sql'


def run(conn, name: str):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def document(conn, project, slug: str, folder: str | None = None, *, with_folder: bool = True):
    doc, version = uuid4(), uuid4()
    if with_folder:
        conn.execute('INSERT INTO ka.documents(id,project_id,slug,folder,current_version_id) VALUES (%s,%s,%s,%s,%s)',
                     (doc, project, slug, folder, version))
    else:
        conn.execute('INSERT INTO ka.documents(id,project_id,slug,current_version_id) VALUES (%s,%s,%s,%s)',
                     (doc, project, slug, version))
    conn.execute("""INSERT INTO ka.document_versions(id,document_id,project_id,version,title,description,purpose,audience,
        markdown,content_hash,source_snapshot,origin) VALUES (%s,%s,%s,1,%s,'','explanation','both','# T',
        encode(sha256(convert_to('# T','UTF8')),'hex'),'{}','manual')""", (version, doc, project, slug))
    return doc


@pytest.fixture
def db(store):
    with store.connect() as conn:
        yield conn, store.project(conn, 'shop')


def test_folder_column_and_per_folder_slug_uniqueness(db):
    conn, project = db
    for folder in ('payments', 'team/payments', None):
        document(conn, project, 'runbook', folder)
    for folder in ('payments', None):  # NULLS NOT DISTINCT: folderless slugs stay unique as before
        with pytest.raises(psycopg.errors.UniqueViolation):
            with conn.transaction():
                document(conn, project, 'runbook', folder)
    for bad in ('.hidden', 'a//b', '/abs', 'a/', '../x', 'my docs', 'team/node_modules', 'services', 'services/x', 'x' * 201):
        with pytest.raises(psycopg.errors.CheckViolation, match='documents_folder_check'):
            with conn.transaction():
                document(conn, project, 'other', bad)
    rows = conn.execute("SELECT folder FROM ka.document_details WHERE slug='runbook' ORDER BY folder NULLS FIRST").fetchall()
    assert [row['folder'] for row in rows] == [None, 'payments', 'team/payments']


def test_down_restores_project_wide_slugs_and_refuses_shared_slugs(db):
    conn, project = db
    document(conn, project, 'runbook', 'payments')
    shared = document(conn, project, 'runbook', 'team')
    conn.commit()  # deferred version checks must not be pending while the table is altered
    with pytest.raises(psycopg.errors.RaiseException, match='share a slug'):
        with conn.transaction():
            run(conn, DOWN)
    conn.execute('DELETE FROM ka.documents WHERE id=%s', (shared,))
    conn.commit()
    run(conn, DOWN)
    assert conn.execute("""SELECT 1 FROM information_schema.columns WHERE table_schema='ka'
        AND table_name='documents' AND column_name='folder'""").fetchone() is None
    assert conn.execute("SELECT count(*) AS n FROM ka.document_details WHERE slug='runbook'").fetchone()['n'] == 1
    with pytest.raises(psycopg.errors.UniqueViolation):
        with conn.transaction():
            document(conn, project, 'runbook', with_folder=False)
    conn.commit()
    run(conn, UP)
    document(conn, project, 'runbook', 'other')
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_document_folders_migration.py -q`
Expected: FAIL with `UndefinedColumn: column "folder" of relation "documents" does not exist`.

- [ ] **Step 3: Write the migration**

`db/migrations/0068_document_folders.up.sql`:

```sql
SET search_path = ka, public;

-- A document can live in a documentation folder under GENERATED_DOCS_FOLDER (<folder>/<slug>/README.md); documents
-- without one export as before (<slug>/README.md). Same segment rule as RAG folders; services/ holds phase exports.
ALTER TABLE documents ADD COLUMN folder text CONSTRAINT documents_folder_check CHECK (
    length(folder) <= 200 AND folder ~ '^[A-Za-z0-9_-][A-Za-z0-9._-]*(/[A-Za-z0-9_-][A-Za-z0-9._-]*)*$'
    AND folder !~ '(^|/)node_modules(/|$)' AND split_part(folder, '/', 1) <> 'services');

-- Slugs are unique per folder; NULLS NOT DISTINCT keeps folderless slugs unique across the project, as before.
ALTER TABLE documents DROP CONSTRAINT documents_project_id_slug_key;
ALTER TABLE documents ADD CONSTRAINT documents_project_id_folder_slug_key UNIQUE NULLS NOT DISTINCT (project_id, folder, slug);

-- d.* is expanded when a view is created, so the view is rebuilt to include the new column.
DROP VIEW document_details;
CREATE VIEW document_details AS
SELECT d.*, v.version, v.title, v.description, v.purpose, v.audience, v.tags, v.content_hash,
    COALESCE((SELECT array_agg(code_project_id ORDER BY code_project_id) FROM document_code_projects WHERE document_id=d.id),'{}') AS code_project_ids,
    COALESCE((SELECT array_agg(service_id ORDER BY service_id) FROM document_services WHERE document_id=d.id),'{}') AS service_ids,
    COALESCE((SELECT array_agg(server_id ORDER BY server_id) FROM document_servers WHERE document_id=d.id),'{}') AS server_ids,
    COALESCE((SELECT array_agg(deployment_id ORDER BY deployment_id) FROM document_deployments WHERE document_id=d.id),'{}') AS deployment_ids,
    COALESCE((SELECT array_agg(environment_id ORDER BY environment_id) FROM document_environments WHERE document_id=d.id),'{}') AS environment_ids
FROM documents d JOIN document_versions v ON v.id=d.current_version_id;
```

`db/migrations/0068_document_folders.down.sql`:

```sql
SET search_path = ka, public;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM documents GROUP BY project_id, slug HAVING count(*) > 1) THEN
        RAISE EXCEPTION 'Documents in different folders share a slug; rename or delete them before rolling back 0068';
    END IF;
END $$;

DROP VIEW document_details;
ALTER TABLE documents DROP CONSTRAINT documents_project_id_folder_slug_key;
ALTER TABLE documents DROP COLUMN folder;
ALTER TABLE documents ADD CONSTRAINT documents_project_id_slug_key UNIQUE (project_id, slug);
CREATE VIEW document_details AS
SELECT d.*, v.version, v.title, v.description, v.purpose, v.audience, v.tags, v.content_hash,
    COALESCE((SELECT array_agg(code_project_id ORDER BY code_project_id) FROM document_code_projects WHERE document_id=d.id),'{}') AS code_project_ids,
    COALESCE((SELECT array_agg(service_id ORDER BY service_id) FROM document_services WHERE document_id=d.id),'{}') AS service_ids,
    COALESCE((SELECT array_agg(server_id ORDER BY server_id) FROM document_servers WHERE document_id=d.id),'{}') AS server_ids,
    COALESCE((SELECT array_agg(deployment_id ORDER BY deployment_id) FROM document_deployments WHERE document_id=d.id),'{}') AS deployment_ids,
    COALESCE((SELECT array_agg(environment_id ORDER BY environment_id) FROM document_environments WHERE document_id=d.id),'{}') AS environment_ids
FROM documents d JOIN document_versions v ON v.id=d.current_version_id;
```

In `db/tests/run.sh`, change `[ "$rolled_back_version" = 66 ]` to
`[ "$rolled_back_version" = 67 ]`.

- [ ] **Step 4: Run the tests and confirm they pass**

Run:
```bash
cd services/admin-api && uv run --frozen pytest tests/test_document_folders_migration.py \
  tests/test_documents.py::test_document_migrations_roundtrip_and_preserve_populated_tables tests/test_data_root_folders_migration.py -q
cd ../.. && bash db/tests/run.sh
```
Expected: PASS; `run.sh` prints its `PASS:` lines.

- [ ] **Step 5: Commit**

```bash
git add db/migrations/0068_document_folders.up.sql db/migrations/0068_document_folders.down.sql db/tests/run.sh \
  services/admin-api/tests/test_document_folders_migration.py
git commit -m "feat(db): 0068 documents get a documentation folder with slugs unique per folder

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Documents — save into a folder, export to `<folder>/<slug>/`, keep the folder on edit

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/documents.py`
- Modify: `services/admin-api/tests/test_documents.py`

**Interfaces:**
- Consumes: Task 2's `documents.folder`.
- Produces: `save_document(conn, project, actor, value, document_id=None, generation_id=None, folder=None) -> dict`
  (a new document gets `folder`; an existing one keeps its stored folder);
  `export_conflict(conn, project, slug, folder, document_id=None) -> str | None`;
  `export_location(slug, phase_metadata=None, folder=None)`;
  `export_document(document)` reads `document.get('folder')`;
  `export_input()` returns `folder`; `remove_exported_folder(slug, folder=None)`;
  `GET /api/documentation/documents[/{id}]` rows carry `folder`.

- [ ] **Step 1: Write the failing tests**

In `services/admin-api/tests/test_documents.py`, append:

```python
def test_folder_documents_export_into_their_folder_and_keep_it_on_edit(store, tmp_path, monkeypatch):
    from knowledge_admin.documents import DocumentInput, export_args, export_document, export_input, save_document
    client = _client(tmp_path, repository_store=store)
    out = tmp_path / 'generated'
    out.mkdir()
    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', str(out))
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        saved = save_document(conn, project, None, DocumentInput(slug='api-reference', title='API reference',
            markdown='# API\n\n```d2\na -> b\n```\n'), folder='team/payments')
        export_document(**export_args(export_input(conn, saved['id'])))
    folder = out / 'team' / 'payments' / 'api-reference'
    assert (folder / 'README.md').read_text().startswith('# API') and (folder / 'architecture-1.d2').is_file()
    assert not (out / 'api-reference').exists()
    endpoint = '/api/documentation/documents/' + str(saved['id'])
    assert client.get(endpoint, headers=HEADERS).json()['folder'] == 'team/payments'
    # The editor sends no folder: the document stays in its folder and a rename moves only its own export.
    edited = client.put(endpoint, headers=HEADERS, json={'slug': 'api-guide', 'title': 'API guide', 'markdown': '# Guide', 'revision': 1})
    assert edited.status_code == 200, edited.text
    assert client.get(endpoint, headers=HEADERS).json()['folder'] == 'team/payments'
    assert (out / 'team/payments/api-guide/README.md').read_text() == '# Guide'
    assert not (out / 'team/payments/api-reference').exists()
    # The same slug may exist once per folder and once without a folder.
    with store.connect() as conn:
        for other in ('other', None):
            save_document(conn, project, None, DocumentInput(slug='api-guide', title='Elsewhere', markdown='# E'), folder=other)
    with pytest.raises(psycopg.errors.UniqueViolation):
        with store.connect() as conn:
            save_document(conn, project, None, DocumentInput(slug='api-guide', title='Twice', markdown='# T'), folder='team/payments')
    assert client.delete(endpoint, headers=HEADERS, params={'revision': 2, 'confirmed': True}).status_code == 200
    assert not (out / 'team/payments/api-guide').exists()


def test_a_folder_never_shares_a_top_level_export_folder_with_a_document(store, tmp_path, monkeypatch):
    from fastapi import HTTPException
    from knowledge_admin.documents import DocumentInput, save_document
    client = _client(tmp_path, repository_store=store)
    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', str(tmp_path))
    assert client.post('/api/documentation/documents', headers=HEADERS,
                       json={'slug': 'payments', 'title': 'P', 'markdown': '# P'}).status_code == 201
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        with pytest.raises(HTTPException) as error:
            save_document(conn, project, None, DocumentInput(slug='api', title='A', markdown='# A'), folder='payments/api')
        assert error.value.status_code == 409
        assert error.value.detail == 'The folder payments holds the export of the document payments. Choose another folder.'
        save_document(conn, project, None, DocumentInput(slug='api', title='A', markdown='# A'), folder='team/payments')
    refused = client.post('/api/documentation/documents', headers=HEADERS, json={'slug': 'team', 'title': 'T', 'markdown': '# T'})
    assert refused.status_code == 409
    assert refused.json()['error']['message'] == 'The slug team is used by a documentation folder. Choose another slug.'
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_documents.py -q`
Expected: FAIL with `TypeError: save_document() got an unexpected keyword argument 'folder'`.

- [ ] **Step 3: Implement in `services/admin-api/src/knowledge_admin/documents.py`**

Replace `export_location` and the first two lines of `export_document`'s body:

```python
def export_location(slug: str, phase_metadata: dict | None = None, folder: str | None = None) -> tuple[Path, str]:
    """Folder and main file of a document's current export: [<folder>/]<slug>/README.md or services/<svc>/<phase>/<phase>.md."""
    root = _output_root()
    if phase_metadata is None:
        return (root / folder / slug if folder else root / slug), 'README.md'
```

(the rest of `export_location` is unchanged), and in `export_document`:

```python
    root = _output_root()
    folder, main = export_location(document['slug'], phase_metadata, document.get('folder'))
```

In `export_input`, select the folder and return it:

```python
    row = conn.execute('''SELECT d.slug, d.folder, d.revision, d.project_id, v.markdown, v.source_snapshot, v.generation_id
        FROM ka.documents d JOIN ka.document_versions v ON v.id=d.current_version_id WHERE d.id=%s''', (document_id,)).fetchone()
```

```python
    return {'slug': row['slug'], 'folder': row['folder'], 'markdown': row['markdown'], 'revision': row['revision'],
            'source_commits': commits, 'phase_metadata': phase_metadata}
```

Replace `remove_exported_folder` and the folderless branch of `remove_export`:

```python
def remove_exported_folder(slug: str, folder: str | None = None) -> None:
    """Remove the regular files of a document's [<folder>/]<slug>/ folder; subdirectories stay, and the folder goes only if empty."""
    root = _output_root()
    parent = root / folder if folder else root
    target = parent / slug
    if target.parent == parent and _real_folder(root, target):
        _remove_files(target)


def remove_export(current: dict) -> None:
    """Remove a document's current export (see export_input); phase folders lose only the exporter's files."""
    phase_metadata = current['phase_metadata']
    if phase_metadata is None:
        remove_exported_folder(current['slug'], current.get('folder'))
        return
```

(the rest of `remove_export` is unchanged). In `backfill_exports`, change
`folder, main = export_location(current['slug'], current['phase_metadata'])` to
`folder, main = export_location(current['slug'], current['phase_metadata'], current['folder'])`.

Add above `save_document`:

```python
def export_conflict(conn, project: UUID, slug: str, folder: str | None, document_id: UUID | None = None) -> str | None:
    """Why this document's export would share a top-level folder of GENERATED_DOCS_FOLDER with another export, or None.
    A folderless document exports to <slug>/, a folder document to <folder>/<slug>/."""
    if folder is None:
        if conn.execute("SELECT 1 FROM ka.documents WHERE project_id=%s AND split_part(folder,'/',1)=%s LIMIT 1",
                        (project, slug)).fetchone():
            return f'The slug {slug} is used by a documentation folder. Choose another slug.'
        return None
    first = folder.split('/')[0]
    if conn.execute('''SELECT 1 FROM ka.documents WHERE project_id=%s AND folder IS NULL AND slug=%s
        AND id IS DISTINCT FROM %s LIMIT 1''', (project, first, document_id)).fetchone():
        return f'The folder {first} holds the export of the document {first}. Choose another folder.'
    return None
```

Change `save_document`'s signature, docstring and the start of its body to:

```python
def save_document(conn, project: UUID, actor: UUID | None, value: DocumentInput,
                  document_id: UUID | None = None, generation_id: UUID | None = None, folder: str | None = None) -> dict:
    """Save a complete immutable version within the caller's transaction.

    `folder` places a new document in a documentation folder; an existing document keeps its own folder."""
    if document_id is None:
        if value.revision is not None:
            raise HTTPException(400, 'New documents cannot reuse a revision')
        document_id = uuid4()
        revision,base = 1,None
    else:
        previous = conn.execute('SELECT revision,current_version_id,folder FROM ka.documents WHERE id=%s AND project_id=%s FOR UPDATE', (document_id,project)).fetchone()
        if previous is None:
            raise HTTPException(404, 'Document not found')
        if previous['revision'] != value.revision:
            raise HTTPException(409, 'This document changed. Reload it before saving; your text has not been overwritten.')
        revision,base = previous['revision']+1,previous['current_version_id']
        folder = previous['folder']
    conflict = export_conflict(conn, project, value.slug, folder, document_id)
    if conflict:
        raise HTTPException(409, conflict)
```

and the documents insert to:

```python
    conn.execute('''INSERT INTO ka.documents(id,project_id,slug,folder,current_version_id,revision) VALUES (%s,%s,%s,%s,%s,%s)
        ON CONFLICT (id) DO UPDATE SET slug=EXCLUDED.slug,current_version_id=EXCLUDED.current_version_id,revision=EXCLUDED.revision''',
        (document_id,project,value.slug,folder,version_id,revision))
```

In the `save` route, track the old folder:

```python
                old_slug = old_folder = None
                if document_id is not None:
                    old = conn.execute('SELECT slug,folder FROM ka.documents WHERE id=%s AND project_id=%s', (document_id, project)).fetchone()
                    if old:
                        old_slug, old_folder = old['slug'], old['folder']
```

and remove the old export with `remove_exported_folder(old_slug, old_folder)`.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_documents.py tests/test_documentation_generation.py tests/test_code_documentation_phases.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/documents.py services/admin-api/tests/test_documents.py
git commit -m "feat(admin-api): documents saved into a documentation folder export to <folder>/<slug>/

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Generation input and queue for `scope='code_project'`; history scope filter

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/documentation_generation.py`
- Modify: `services/admin-api/src/knowledge_admin/documents.py` (`GET /generations`)
- Create: `services/admin-api/tests/test_project_documentation.py`

**Interfaces:**
- Consumes: Task 1's `documentation_folder`, `document_slug`; Task 3's `export_conflict`,
  `save_document(folder=)`.
- Produces: `GenerationInput` with the code project fields; `SAME_DOCUMENT_NAME`;
  `queue_code_project(conn, project, actor, model, value) -> dict`; generation rows with
  `agent_snapshot.project`, `prompt_snapshot[0].kind == 'project'` and
  `source_snapshot.targets == [{'kind': 'project', 'slug', 'folder', 'existing'}]`;
  `GET /api/documentation/generations?scope=` with `scope`, `document_name`, `folder`.

- [ ] **Step 1: Write the failing tests**

Create `services/admin-api/tests/test_project_documentation.py`:

```python
"""One code project, a chosen prompt and comments, one document saved into a documentation folder."""
import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from knowledge_admin import documentation_generation as generation
from knowledge_admin.code_processing import CodeProcessingError
from knowledge_admin.data_root import FOLDER_RULE
from knowledge_admin.documents import DocumentInput, save_document
from test_documentation_generation import setup_request
from test_repositories import HEADERS

BASE = {'scope': 'code_project', 'model_id': str(uuid4()), 'code_project_id': str(uuid4()), 'prompt_id': str(uuid4()),
        'prompt_version_id': str(uuid4()), 'document_name': ' Payments – API reference ', 'folder': 'team/payments'}


def project_request(store, tmp_path, monkeypatch):
    """The generation fixture's code project with the code analysis prompt, comments, a name and a folder."""
    client, value, _ = setup_request(store, tmp_path, monkeypatch)
    prompts = {p['name']: p for p in client.get('/api/documentation/prompts', headers=HEADERS).json()['prompts']}
    prompt = prompts['Code analysis and architecture']
    project = value['code_project_ids'][0]
    request = {'scope': 'code_project', 'model_id': value['model_id'], 'code_project_id': project,
               'prompt_id': prompt['id'], 'prompt_version_id': prompt['active_version_id'],
               'code_graph_hashes': {project: 'fixture'}, 'comments': 'Focus on the public API.',
               'document_name': 'Payments – API reference', 'folder': 'team/payments'}
    return client, request, prompts, value


def post(client, request, **change):
    return client.post('/api/documentation/generations', headers=HEADERS, json={**request, **change})


def test_code_project_input_is_validated():
    value = generation.GenerationInput(**BASE)
    assert (value.document_name, value.folder, value.units, value.comments) == ('Payments – API reference', 'team/payments', [], '')
    for change in ({'folder': '../payments'}, {'folder': '.hidden'}, {'folder': 'a//b'}, {'folder': '/srv/docs'},
                   {'folder': 'team/node_modules'}, {'folder': 'services/payments'}, {'folder': 'my docs'},
                   {'folder': 'x' * 201}, {'folder': None}, {'document_name': '  '}, {'document_name': '– –'},
                   {'document_name': None}, {'document_name': 'x' * 241}, {'comments': 'x' * 20001}, {'comments': 'a\x00b'},
                   {'code_project_id': None}, {'prompt_id': None}, {'prompt_version_id': None},
                   {'service_id': str(uuid4())}, {'environment_id': str(uuid4())}, {'server_ids': [str(uuid4())]},
                   {'code_project_ids': [str(uuid4())]}, {'instructions': 'Use comments instead'},
                   {'workflow_id': str(uuid4())},
                   {'units': [{'kind': 'code', 'prompt_id': str(uuid4()), 'prompt_version_id': str(uuid4())}]}):
        with pytest.raises(ValidationError):
            generation.GenerationInput(**{**BASE, **change})
    unit = {'kind': 'code', 'prompt_id': str(uuid4()), 'prompt_version_id': str(uuid4())}
    service = {'scope': 'service', 'model_id': str(uuid4()), 'service_id': str(uuid4())}
    generation.GenerationInput(**service, units=[unit])
    for change in ({'units': []}, {'units': [unit], 'folder': 'payments'}, {'units': [unit], 'comments': 'x'},
                   {'units': [unit], 'document_name': 'Doc'}, {'units': [unit], 'code_project_id': str(uuid4())}):
        with pytest.raises(ValidationError):
            generation.GenerationInput(**{**service, **change})


def test_code_project_generation_snapshots_the_prompt_comments_and_code_tools_only(store, tmp_path, monkeypatch):
    client, request, prompts, _ = project_request(store, tmp_path, monkeypatch)
    response = post(client, request)
    assert response.status_code == 202, response.text
    detail = client.get('/api/documentation/prompts/' + request['prompt_id'], headers=HEADERS).json()
    active = next(v['content'] for v in detail['versions'] if v['id'] == detail['active_version_id'])
    with store.connect() as conn:
        row = conn.execute('''SELECT mode,instructions,agent_snapshot,prompt_snapshot,source_snapshot
            FROM ka.documentation_generations''').fetchone()
        assert conn.execute('SELECT 1 FROM ka.documentation_workflow_jobs').fetchone() is None
    assert set(row['agent_snapshot']) == {'project'}
    config = row['agent_snapshot']['project']
    assert config['id'] == 'documentation-project' and config['model']['id'] == request['model_id']
    assert config['system_prompt']['content'] == active + '\n\n## Comments for this run\n\nFocus on the public API.'
    assert config['code']['enabled'] and [p['id'] for p in config['code']['projects']] == [request['code_project_id']]
    assert config['code']['projects'][0]['folder_paths'] == ['/work/code/app'] and len(config['code']['graphs']) == 1
    assert config['ssh'] == {'enabled': False, 'servers': []}
    assert not any(config[key]['enabled'] for key in ('documents', 'mcp', 'skills', 'services'))
    assert (row['mode'], row['instructions']) == ('create', 'Focus on the public API.')
    prompt = row['prompt_snapshot'][0]
    assert (prompt['kind'], prompt['id'], prompt['version_id'], prompt['comments'], prompt['overridden']) == (
        'project', request['prompt_id'], request['prompt_version_id'], 'Focus on the public API.', False)
    sources = row['source_snapshot']
    assert sources['scope'] == 'code_project' and sources['code_project_ids'] == [request['code_project_id']]
    assert sources['targets'] == [{'kind': 'project', 'slug': 'payments-api-reference', 'folder': 'team/payments', 'existing': None}]
    history = client.get('/api/documentation/generations', headers=HEADERS, params={'scope': 'code_project'}).json()['generations']
    assert [(h['scope'], h['document_name'], h['folder'], h['status']) for h in history] == [
        ('code_project', 'Payments – API reference', 'team/payments', 'queued')]
    assert client.get('/api/documentation/generations', headers=HEADERS, params={'scope': 'service'}).json()['generations'] == []
    # A second run for the same project waits for the first.
    duplicate = post(client, request, document_name='Other name')
    assert duplicate.status_code == 409
    assert duplicate.json()['error']['message'] == 'Documentation for this code project or document is already queued or running'
    # Without comments the system prompt is the prompt alone.
    with store.connect() as conn:
        conn.execute("UPDATE ka.jobs SET status='succeeded'")
    assert post(client, request, comments='', document_name='Second').status_code == 202
    with store.connect() as conn:
        latest = conn.execute('''SELECT g.agent_snapshot FROM ka.documentation_generations g JOIN ka.jobs j ON j.id=g.job_id
            ORDER BY j.created_at DESC LIMIT 1''').fetchone()
    assert latest['agent_snapshot']['project']['system_prompt']['content'] == active


def test_code_project_generation_refuses_wrong_prompts_graphs_names_and_folders(store, tmp_path, monkeypatch):
    client, request, prompts, _ = project_request(store, tmp_path, monkeypatch)
    server = prompts['Deployment and service operations']
    refused = post(client, request, prompt_id=server['id'], prompt_version_id=server['active_version_id'])
    assert refused.status_code == 400
    assert refused.json()['error']['message'] == 'Select an active documentation prompt for code (code or mixed input)'
    stale = post(client, request, prompt_version_id=str(uuid4()))
    assert stale.status_code == 409 and stale.json()['error']['message'] == 'The prompt changed. Reload the prompt before generating.'
    bad = post(client, request, folder='../payments')
    assert bad.status_code == 400 and FOLDER_RULE in bad.json()['error']['message']
    assert post(client, request, code_project_id=str(uuid4())).status_code == 400
    assert client.post('/api/documentation/documents', headers=HEADERS,
                       json={'slug': 'team', 'title': 'Team', 'markdown': '# Team'}).status_code == 201
    shadow = post(client, request)
    assert shadow.status_code == 409
    assert shadow.json()['error']['message'] == 'The folder team holds the export of the document team. Choose another folder.'
    def missing(project, expected_hash=None):
        raise CodeProcessingError('Generate and inspect the code graph in Phase 1 before generating documentation.')
    monkeypatch.setattr(generation, 'load_code_graph', missing)
    no_graph = post(client, request, folder='payments')
    assert no_graph.status_code == 409 and 'Phase 1' in no_graph.json()['error']['message']
    assert not generation.run_once(store)


def test_service_generation_ignores_folder_documents_with_the_same_slug(store, tmp_path, monkeypatch):
    client, value, _ = setup_request(store, tmp_path, monkeypatch)
    code = next(u for u in value['units'] if u['kind'] == 'code')
    slug = 'application-' + value['service_id'][:8] + '-code'
    with store.connect() as conn:
        save_document(conn, store.project(conn, 'shop'), None,
                      DocumentInput(slug=slug, title='Folder copy', markdown='# Folder copy'), folder='payments')
    response = client.post('/api/documentation/generations', headers=HEADERS,
                           json={**value, 'units': [code], 'environment_id': None, 'server_ids': []})
    assert response.status_code == 202, response.text
    with store.connect() as conn:
        target = conn.execute('SELECT source_snapshot FROM ka.documentation_generations').fetchone()['source_snapshot']['targets'][0]
    assert target['slug'] == slug and target['existing'] is None
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_project_documentation.py -q`
Expected: FAIL. `GenerationInput` refuses `scope='code_project'` (literal error), the
service test gets 409 "These documents already exist" because the slug lookup finds the
folder document.

- [ ] **Step 3: Implement the input model**

In `services/admin-api/src/knowledge_admin/documentation_generation.py`, change the
documents import and add the data-root import:

```python
from knowledge_admin.data_root import documentation_folder
from knowledge_admin.documents import DocumentInput, GitCommit, document_slug, export_conflict, export_document, save_document
```

Replace `class GenerationInput` with:

```python
class GenerationInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    workflow_id: UUID | None = None
    scope: Literal['environment', 'service', 'code_project']
    model_id: UUID
    service_id: UUID | None = None
    environment_id: UUID | None = None
    code_project_ids: list[UUID] = Field(default_factory=list, max_length=100)
    server_ids: list[UUID] = Field(default_factory=list, max_length=100)
    units: list[GenerationUnit] = Field(default_factory=list, max_length=4)
    code_graph_hashes: dict[str, str] = Field(default_factory=dict, max_length=100)
    source_commits: dict[UUID, GitCommit] = Field(default_factory=dict, max_length=100)
    instructions: str = Field(default='', max_length=20000)
    update_existing: bool = False
    # scope='code_project': one project, one chosen prompt plus this run's comments, one named document in a folder.
    code_project_id: UUID | None = None
    prompt_id: UUID | None = None
    prompt_version_id: UUID | None = None
    comments: str = Field(default='', max_length=20000)
    document_name: str | None = Field(default=None, max_length=240)
    folder: str | None = None

    @model_validator(mode='after')
    def validate_scope(self):
        if self.scope == 'code_project':
            return self.validate_code_project()
        if not self.units:
            raise ValueError('Select at least one documentation step')
        if (self.code_project_id or self.prompt_id or self.prompt_version_id or self.comments
                or self.document_name is not None or self.folder is not None):
            raise ValueError('A prompt, comments, a document name and a folder are only used by code project documentation')
        kinds = [unit.kind for unit in self.units]
        # … the rest of the previous validate_scope body, unchanged …
        return self

    def validate_code_project(self):
        if (self.code_project_id is None or self.prompt_id is None or self.prompt_version_id is None or self.units
                or self.workflow_id or self.service_id or self.environment_id or self.code_project_ids
                or self.server_ids or self.instructions):
            raise ValueError('Code project documentation takes one code project and one prompt; put extra instructions in the comments')
        name = (self.document_name or '').strip()
        if '\x00' in self.comments or '\x00' in name:
            raise ValueError('Comments and the document name cannot contain null characters')
        if not document_slug(name):
            raise ValueError('Enter a document name with letters or digits')
        self.document_name = name
        self.folder = documentation_folder(self.folder or '')
        return self
```

(Keep every line of the previous `validate_scope` body from `kinds = …` to `return self`;
only the two guards above it are new.)

- [ ] **Step 4: Implement the queue**

Add above `build_api`:

```python
SAME_DOCUMENT_NAME = 'A document with this name already exists in this folder. Confirm to save a new version.'


def queue_code_project(conn, project: UUID, actor: UUID, model: dict, value: GenerationInput) -> dict:
    """Queue one code project's document: the chosen prompt plus comments, code and graph tools only, saved in a folder."""
    code_projects = conn.execute("""SELECT r.id,r.name,r.description,r.remote_url AS remote_git_url,r.current_commit,
        ARRAY(SELECT path::text FROM ka.code_project_folders f WHERE f.code_project_id=r.id ORDER BY path) AS folder_paths
        FROM ka.repositories r WHERE project_id=%s AND id=%s AND archived_at IS NULL""", (project, value.code_project_id)).fetchall()
    if not code_projects:
        raise HTTPException(400, 'Select an available code project')
    code_root = PurePosixPath(os.environ.get('CODE_ROOT', ''))
    if not code_root.is_absolute() or '..' in code_root.parts:
        raise HTTPException(503, 'CODE_ROOT must be an absolute mounted path')
    for code_project in code_projects:
        code_project['folder_paths'] = [str(code_root / path) for path in code_project['folder_paths']]
    commits = collect_source_commits(code_projects)
    for code_project in code_projects:
        code_project['current_commit'] = commits.get(str(code_project['id']))
    if any(commits.get(str(key)) != commit for key, commit in value.source_commits.items()):
        raise HTTPException(409, 'A requested source commit does not match a clean selected checkout')
    value.source_commits = {UUID(key): commit for key, commit in commits.items()}
    try:
        graphs = [load_code_graph(p, value.code_graph_hashes.get(str(p['id']))) for p in code_projects]
    except CodeProcessingError as exc:
        raise HTTPException(409, str(exc)) from None
    prompt = conn.execute("""SELECT p.id,p.name,p.input_kind,v.id AS version_id,v.version,v.content
        FROM ka.documentation_prompts p JOIN ka.documentation_prompt_versions v ON v.id=p.active_version_id
        WHERE p.id=%s AND p.project_id=%s AND p.enabled""", (value.prompt_id, project)).fetchone()
    if prompt is None or prompt['input_kind'] not in ('code', 'mixed'):
        raise HTTPException(400, 'Select an active documentation prompt for code (code or mixed input)')
    if prompt['version_id'] != value.prompt_version_id:
        raise HTTPException(409, 'The prompt changed. Reload the prompt before generating.')
    slug = document_slug(value.document_name)
    conflict = export_conflict(conn, project, slug, value.folder)
    if conflict:
        raise HTTPException(409, conflict)
    existing = conn.execute("""SELECT d.id,d.revision,v.markdown FROM ka.documents d
        JOIN ka.document_versions v ON v.id=d.current_version_id WHERE d.project_id=%s AND d.folder=%s AND d.slug=%s""",
        (project, value.folder, slug)).fetchone()
    if existing is not None and not value.update_existing:
        raise HTTPException(409, SAME_DOCUMENT_NAME)
    if conn.execute("""SELECT 1 FROM ka.documentation_generations g JOIN ka.jobs j ON j.id=g.job_id
        WHERE g.project_id=%s AND j.status IN ('queued','running') AND g.source_snapshot->>'scope'='code_project'
        AND (g.source_snapshot->>'code_project_id'=%s OR
             (g.source_snapshot->>'folder'=%s AND g.source_snapshot->'targets'->0->>'slug'=%s))""",
        (project, str(value.code_project_id), value.folder, slug)).fetchone():
        raise HTTPException(409, 'Documentation for this code project or document is already queued or running')
    system_prompt = prompt['content'] + (f'\n\n## Comments for this run\n\n{value.comments}' if value.comments.strip() else '')
    configs = {'project': jsonable_encoder({
        'id': 'documentation-project', 'name': 'Documentation: code project',
        'description': 'Built-in documentation step', 'enabled': True, 'model': model,
        'system_prompt': {'content': system_prompt}, 'instructions': '',
        'code': {'enabled': True, 'projects': code_projects, 'graphs': graphs},
        'ssh': {'enabled': False, 'servers': []},
        'documents': {'enabled': False, 'all_collections': False, 'collections': []}, 'mcp': {'enabled': False, 'servers': []},
        'skills': {'enabled': False, 'items': []}, 'services': {'enabled': False, 'items': []},
    })}
    prompts = [{**prompt, 'active_content': prompt['content'], 'overridden': False, 'kind': 'project', 'comments': value.comments}]
    sources = jsonable_encoder({**value.model_dump(), 'workflow_version': 2, 'code_project_ids': [value.code_project_id],
        'service': None, 'services': [], 'environment': None, 'code_projects': code_projects,
        'targets': [{'kind': 'project', 'slug': slug, 'folder': value.folder, 'existing': existing}]})
    job_id = uuid4()
    conn.execute("""INSERT INTO ka.jobs(id,project_id,kind,requested_by,idempotency_key)
        VALUES (%s,%s,'documentation_generate',%s,%s)""", (job_id, project, actor, str(job_id)))
    conn.execute("""INSERT INTO ka.documentation_generations(job_id,project_id,mode,instructions,agent_snapshot,prompt_snapshot,source_snapshot)
        VALUES (%s,%s,%s,%s,%s,%s,%s)""", (job_id, project, 'update' if value.update_existing else 'create', value.comments,
        Jsonb(configs), Jsonb(jsonable_encoder(prompts)), Jsonb(sources)))
    conn.execute("""INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id)
        VALUES (%s,%s,'documentation.generation_requested','job',%s)""", (project, actor, job_id))
    return {'job_id': job_id}
```

In `build_api`'s `generate`, directly after the line that sets `model['api_key_ref']`,
add:

```python
            if value.scope == 'code_project':
                return queue_code_project(conn, project, actor, model, value)
```

In the service/environment slug lookup further down, scope it to folderless documents:

```python
                existing = conn.execute("""SELECT d.id,d.revision,v.markdown FROM ka.documents d
                    JOIN ka.document_versions v ON v.id=d.current_version_id WHERE d.project_id=%s AND d.folder IS NULL AND d.slug=%s""", (project,slug)).fetchone()
```

- [ ] **Step 5: The history scope filter in `documents.py`**

Replace the `generations` route's signature, its `SELECT` head and its `WHERE` tail:

```python
    @router.get('/generations')
    def generations(offset: int = Query(default=0, ge=0), service_id: UUID | None = None,
                    kind: str | None = Query(default=None, max_length=80), workflow_id: UUID | None = None,
                    scope: Literal['service', 'environment', 'code_project'] | None = None) -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn,app_name)
            rows = conn.execute('''SELECT g.job_id,g.mode,g.instructions,g.created_at,j.status,j.error,j.result,
                g.source_snapshot->'service_id' AS service_id, g.source_snapshot->'code_graph_hashes' AS code_graph_hashes,
                g.source_snapshot->>'scope' AS scope, g.source_snapshot->>'document_name' AS document_name,
                g.source_snapshot->>'folder' AS folder,
                g.prompt_snapshot,
```

(the subselects stay unchanged), and:

```python
                AND (%s::text IS NULL OR g.prompt_snapshot @> jsonb_build_array(jsonb_build_object('kind',%s::text)))
                AND (%s::text IS NULL OR g.source_snapshot->>'scope'=%s)
                ORDER BY g.created_at DESC,g.job_id LIMIT 50 OFFSET %s''', (project,service_id,str(service_id),workflow_id,workflow_id,kind,kind,scope,scope,offset)).fetchall()
```

- [ ] **Step 6: Run the tests and confirm they pass**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_project_documentation.py tests/test_documentation_generation.py tests/test_code_documentation_phases.py tests/test_documentation_workflows.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add services/admin-api/src/knowledge_admin/documentation_generation.py services/admin-api/src/knowledge_admin/documents.py \
  services/admin-api/tests/test_project_documentation.py
git commit -m "feat(admin-api): queue code project documentation with a chosen prompt, comments, name and folder

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Worker — run the `project` unit, save in the folder, export to `<folder>/<slug>/`

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/documentation_generation.py` (`generate_documents`, `run_once`)
- Modify: `services/admin-api/tests/test_project_documentation.py`

**Interfaces:**
- Consumes: Task 4's snapshots; Task 3's `save_document(folder=)` and folder exports.
- Produces: a saved `purpose='code'` document (title = document name, linked to the code
  project, source commits recorded, `folder` set) with version history; job result
  `{documents, output_folders: ['<root>/<folder>/<slug>'], export_error, phase_metadata: None}`.

- [ ] **Step 1: Write the failing tests**

Append to `services/admin-api/tests/test_project_documentation.py` (add
`from pathlib import Path` and `from types import SimpleNamespace` to the imports):

```python
def history(client):
    return client.get('/api/documentation/generations', headers=HEADERS, params={'scope': 'code_project'}).json()['generations']


def test_code_project_document_is_saved_linked_and_exported_into_its_folder(store, tmp_path, monkeypatch):
    client, request, _, _ = project_request(store, tmp_path, monkeypatch)
    calls = []
    async def run(config, task, **kwargs):
        calls.append((config, json.loads(task), kwargs))
        return SimpleNamespace(final_output=generation.GeneratedDocument(title='Model title', description='API reference',
            markdown='# Payments API\n\n```d2\napi -> db\n```\n'))
    monkeypatch.setattr(generation, 'agent', run)
    assert post(client, request).status_code == 202
    assert generation.run_once(store)
    [(config, task, kwargs)] = calls
    assert kwargs['output_type'] is generation.GeneratedDocument and kwargs['max_turns'] == 60
    assert config['code']['enabled'] and not config['ssh']['enabled'] and not config['mcp']['enabled']
    assert not config['documents']['enabled'] and config['system_prompt']['content'].endswith('Focus on the public API.')
    assert task['document_kind'] == 'project' and task['existing_document'] is None
    assert task['sources']['code_project']['id'] == request['code_project_id']
    assert task['sources']['document_name'] == request['document_name'] and 'comments' not in task['sources']
    [run_row] = history(client)
    assert run_row['status'] == 'succeeded' and run_row['result']['export_error'] is None
    folder = tmp_path / 'team' / 'payments' / 'payments-api-reference'
    assert [Path(p) for p in run_row['result']['output_folders']] == [folder.resolve()]
    assert (folder / 'README.md').read_text().startswith('# Payments API') and (folder / 'architecture-1.d2').is_file()
    assert not (tmp_path / 'payments-api-reference').exists()
    doc_id = run_row['result']['documents'][0]['id']
    document = client.get('/api/documentation/documents/' + doc_id, headers=HEADERS).json()
    assert (document['title'], document['slug'], document['folder'], document['purpose']) == (
        'Payments – API reference', 'payments-api-reference', 'team/payments', 'code')
    assert document['code_project_ids'] == [request['code_project_id']] and document['service_ids'] == []
    assert document['environment_ids'] == [] and document['server_ids'] == []
    assert document['current_version']['origin'] == 'generated'
    with store.connect() as conn:
        recorded = conn.execute('''SELECT g.agent_snapshot,g.prompt_snapshot FROM ka.documentation_generations g
            JOIN ka.document_versions v ON v.generation_id=g.job_id WHERE v.document_id=%s''', (doc_id,)).fetchone()
    assert recorded['agent_snapshot']['project']['model']['id'] == request['model_id']
    assert recorded['prompt_snapshot'][0]['version_id'] == request['prompt_version_id']


def test_same_name_in_the_same_folder_is_refused_or_saved_as_a_new_version(store, tmp_path, monkeypatch):
    client, request, _, _ = project_request(store, tmp_path, monkeypatch)
    async def run(config, task, **kwargs):
        return SimpleNamespace(final_output=generation.GeneratedDocument(title='T', description='',
            markdown='# ' + json.loads(task)['sources']['document_name']))
    monkeypatch.setattr(generation, 'agent', run)
    def generate(**change):
        response = post(client, request, **change)
        if response.status_code == 202:
            assert generation.run_once(store)
        return response
    assert generate().status_code == 202
    refused = generate()
    assert refused.status_code == 409 and refused.json()['error']['message'] == generation.SAME_DOCUMENT_NAME
    assert generate(update_existing=True).status_code == 202
    assert generate(folder='other').status_code == 202
    docs = client.get('/api/documentation/documents', headers=HEADERS).json()['documents']
    assert sorted((d['folder'], d['slug'], d['revision']) for d in docs) == [
        ('other', 'payments-api-reference', 1), ('team/payments', 'payments-api-reference', 2)]
    assert (tmp_path / 'other/payments-api-reference/README.md').is_file()
    assert (tmp_path / 'team/payments/payments-api-reference/README.md').is_file()
    assert [row['mode'] for row in history(client)] == ['create', 'update', 'create']


def test_code_project_document_is_not_saved_when_the_graph_changes_during_the_run(store, tmp_path, monkeypatch):
    client, request, _, _ = project_request(store, tmp_path, monkeypatch)
    assert post(client, request).status_code == 202
    def changed(project, expected_hash=None):
        raise CodeProcessingError('Source or graph selection changed. Generate and inspect the graph again before continuing.')
    async def run(config, task, **kwargs):
        monkeypatch.setattr(generation, 'load_code_graph', changed)
        return SimpleNamespace(final_output=generation.GeneratedDocument(title='T', description='', markdown='# Doc'))
    monkeypatch.setattr(generation, 'agent', run)
    assert generation.run_once(store)
    [run_row] = history(client)
    assert run_row['status'] == 'failed' and 'Generate and inspect the graph again' in run_row['error']
    assert client.get('/api/documentation/documents', headers=HEADERS).json()['total'] == 0
    assert not (tmp_path / 'team').exists()
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_project_documentation.py -q`
Expected: the three new tests FAIL: the worker raises `KeyError: 'name'` (the graph
fallback reads `sources['service']['name']`) or builds `DocumentInput` with
`service_ids=[None]`, so the jobs end `failed`.

- [ ] **Step 3: Implement `generate_documents`**

In `generate_documents`:
1. Change `if kind in ('overview','api','messaging','code','diagram'):` (the block that
   loads `graphs`) to `if kind in ('overview','api','messaging','code','diagram','project'):`.
2. In that block, make the no-source fallback refuse for `project`:

```python
            if not any(g['source_file_count'] for g in graphs):
                if kind == 'project':
                    raise CodeProcessingError('The code project has no readable source files. Synchronize its folders and generate the graph again.')
                document = GeneratedDocument(title=sources['service']['name'] + …  # unchanged from here
```

3. Replace `unit_sources = {k: v for k, v in sources.items() if k not in ('targets','units','service','supporting_documents')}` with:

```python
        if kind == 'project':
            # One project and its graph; the chosen prompt and this run's comments are the system prompt.
            project = sources['code_projects'][0]
            unit_sources = {'code_project': {k: project[k] for k in ('id','name','description','remote_git_url','current_commit')},
                'code_graph_hashes': sources['code_graph_hashes'], 'document_name': sources['document_name']}
        else:
            unit_sources = {k: v for k, v in sources.items() if k not in ('targets','units','service','supporting_documents')}
```

4. In the `agent(...)` call, change the limits to
   `timeout_seconds=1200 if kind in ('code','messaging','project') else 600,` and
   `max_turns=20 if kind == 'overview' else 40 if kind == 'messaging' else 60 if kind in ('code','project') else 30,`.
5. In the final `else:` branch after the result, add the `project` case:

```python
        else:
            document = GeneratedDocument.model_validate(result.final_output)
            if kind == 'code':
                code_analysis = document.markdown
            elif kind == 'project':
                document.title = sources['document_name']
                # Do not publish documentation against a graph whose source changed during the run.
                for project in sources['code_projects']:
                    await asyncio.to_thread(load_code_graph, project, sources['code_graph_hashes'].get(str(project['id'])))
            elif kind == 'deployment':
```

- [ ] **Step 4: Implement `run_once`**

In the save loop, build the project document separately, pass the folder, and export it:

```python
                existing = target['existing']
                if target['kind'] == 'project':
                    value = DocumentInput(slug=target['slug'], title=document.title, description=document.description,
                        markdown=document.markdown, purpose='code', code_project_ids=sources['code_project_ids'],
                        source_commits=commits, revision=existing['revision'] if existing is not None else None)
                else:
                    value = DocumentInput(…)  # the existing construction, unchanged
                saved.append(save_document(conn, job['project_id'], job['requested_by'], value,
                    UUID(existing['id']) if existing is not None else None, job['id'], folder=target.get('folder')))
                exports.append({'slug': value.slug, 'folder': target.get('folder'), 'revision': saved[-1]['revision'],
                                'markdown': value.markdown,
                                'source_commits': {str(key): commit for key, commit in (value.source_commits or {}).items()}})
```

In the failure mapping, before the `AuthenticationError` case, add:

```python
        elif isinstance(exc, psycopg.errors.UniqueViolation):
            error = 'Another document with the same name was saved meanwhile. Generate again and confirm a new version.'
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_project_documentation.py tests/test_documentation_generation.py tests/test_code_documentation_phases.py tests/test_documentation_workflows.py tests/test_documents.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/admin-api/src/knowledge_admin/documentation_generation.py services/admin-api/tests/test_project_documentation.py
git commit -m "feat(admin-api): documentation worker saves code project documents into their folder

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Folders API — list folders and open one (read-only, no RAG calls)

**Files:**
- Create: `services/admin-api/src/knowledge_admin/documentation_folders.py`
- Modify: `services/admin-api/src/knowledge_admin/api.py`
- Create: `services/admin-api/tests/test_documentation_folders.py`

**Interfaces:**
- Consumes: Task 1's `documentation_folder`, `FOLDER_RULE`, `generated_docs_folder`;
  Task 2's `documents.folder`.
- Produces: `GET /api/documentation/folders` and `GET /api/documentation/folders/{folder}`
  with the response shapes in the Global Constraints. Neither route calls RAG.

- [ ] **Step 1: Write the failing tests**

Create `services/admin-api/tests/test_documentation_folders.py`:

```python
"""Documentation folders: listed from the database for browsing; indexing happens in RAG → Collections."""
from uuid import uuid4

import pytest

from knowledge_admin.data_root import FOLDER_RULE
from knowledge_admin.documents import DocumentInput, save_document
from test_api import _client
from test_repositories import HEADERS


@pytest.fixture
def folders(store, tmp_path, monkeypatch):
    """Two documents in payments, one in team/payments and one without a folder, all for the Payments API project."""
    output = tmp_path / 'data' / 'generated-documents'
    output.mkdir(parents=True)
    monkeypatch.setenv('DATA_ROOT_DIR', str(tmp_path / 'data'))
    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', str(output))
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        repo = uuid4()
        conn.execute("INSERT INTO ka.repositories(id,project_id,slug,name) VALUES (%s,%s,'payments-api','Payments API')", (repo, project))
        for slug, folder in (('api-reference', 'payments'), ('runbook', 'payments'), ('api-reference', 'team/payments'), ('handbook', None)):
            save_document(conn, project, None, DocumentInput(slug=slug, title=slug.replace('-', ' ').capitalize(),
                          markdown='# ' + slug, code_project_ids=[repo]), folder=folder)
    return _client(tmp_path, repository_store=store)


def test_folders_list_their_documents_projects_and_rag_folder(folders, monkeypatch):
    body = folders.get('/api/documentation/folders', headers=HEADERS).json()
    assert body['generated_folder'] == 'generated-documents'
    assert [(f['folder'], f['document_count'], f['code_projects'], f['last_generated_at'], f['source_path'])
            for f in body['folders']] == [
        ('payments', 2, ['Payments API'], None, 'generated-documents/payments'),
        ('team/payments', 1, ['Payments API'], None, 'generated-documents/team/payments')]
    detail = folders.get('/api/documentation/folders/team/payments', headers=HEADERS).json()
    assert detail['folder'] == 'team/payments'
    assert [(d['slug'], d['title'], d['code_projects'], d['last_generated_at']) for d in detail['documents']] == [
        ('api-reference', 'Api reference', ['Payments API'], None)]
    assert folders.get('/api/documentation/folders/missing', headers=HEADERS).status_code == 404
    # Without a RAG path for the export folder the folders are still listed.
    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', '/elsewhere/generated-documents')
    elsewhere = folders.get('/api/documentation/folders', headers=HEADERS).json()
    assert elsewhere['generated_folder'] is None and [f['source_path'] for f in elsewhere['folders']] == [None, None]


@pytest.mark.parametrize('folder', ['.hidden', 'team/.git', 'a%20b', 'team/node_modules', 'services/payments', 'x' * 201])
def test_folder_routes_reject_bad_names(folders, folder):
    response = folders.get(f'/api/documentation/folders/{folder}', headers=HEADERS)
    assert response.status_code == 400 and response.json()['error']['message'] == FOLDER_RULE


def test_folders_require_admin(folders):
    assert folders.get('/api/documentation/folders').status_code == 403
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_documentation_folders.py -q`
Expected: FAIL with 404 for every `/api/documentation/folders` route (the 403 test passes).

- [ ] **Step 3: Create `services/admin-api/src/knowledge_admin/documentation_folders.py`**

```python
"""Documentation folders under GENERATED_DOCS_FOLDER, for browsing. Indexing happens in RAG → Collections; these routes
never call RAG."""
from fastapi import APIRouter, HTTPException

from knowledge_admin.data_root import FOLDER_RULE, documentation_folder, generated_docs_folder
from knowledge_admin.repositories import RepositoryStore


def valid_folder(folder: str) -> str:
    try:
        return documentation_folder(folder)
    except ValueError:
        raise HTTPException(400, FOLDER_RULE) from None


def build_api(app_name: str, store: RepositoryStore | None) -> APIRouter:
    router = APIRouter(prefix='/documentation/folders')

    def database() -> RepositoryStore:
        if store is None:
            raise HTTPException(503, 'Document storage is not configured')
        return store

    @router.get('')
    def listing() -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app_name)
            rows = conn.execute('''SELECT f.folder, f.document_count, f.updated_at,
                (SELECT max(v.created_at) FROM ka.documents d JOIN ka.document_versions v ON v.document_id=d.id
                    WHERE d.project_id=%s AND d.folder=f.folder AND v.origin='generated') AS last_generated_at,
                COALESCE((SELECT array_agg(DISTINCT r.name ORDER BY r.name) FROM ka.documents d
                    JOIN ka.document_code_projects l ON l.document_id=d.id JOIN ka.repositories r ON r.id=l.code_project_id
                    WHERE d.project_id=%s AND d.folder=f.folder),'{}') AS code_projects
                FROM (SELECT folder, count(*) AS document_count, max(updated_at) AS updated_at FROM ka.documents
                    WHERE project_id=%s AND folder IS NOT NULL GROUP BY folder) f ORDER BY f.folder''',
                (project, project, project)).fetchall()
        try:
            generated = generated_docs_folder()
        except ValueError:
            generated = None  # the export folder is outside DATA_ROOT: RAG cannot see it
        return {'generated_folder': generated, 'folders': [
            {**row, 'source_path': f'{generated}/{row["folder"]}' if generated else None} for row in rows]}

    @router.get('/{folder:path}')
    def folder_documents(folder: str) -> dict:
        folder = valid_folder(folder)
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app_name)
            documents = conn.execute('''SELECT d.id,d.slug,d.title,d.version,d.updated_at,
                (SELECT max(v.created_at) FROM ka.document_versions v WHERE v.document_id=d.id AND v.origin='generated') AS last_generated_at,
                COALESCE((SELECT array_agg(r.name ORDER BY lower(r.name)) FROM ka.repositories r WHERE r.id=ANY(d.code_project_ids)),'{}') AS code_projects
                FROM ka.document_details d WHERE d.project_id=%s AND d.folder=%s ORDER BY lower(d.title),d.id''', (project, folder)).fetchall()
        if not documents:
            raise HTTPException(404, 'Documentation folder not found')
        return {'folder': folder, 'documents': documents}

    return router
```

- [ ] **Step 4: Register it in `services/admin-api/src/knowledge_admin/api.py`**

Add `documentation_folders` to the `from knowledge_admin import …` line, and after
`router.include_router(documentation_prompts.build_api(app_name, repository_store))` add:

```python
    router.include_router(documentation_folders.build_api(app_name, repository_store))
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_documentation_folders.py tests/test_api.py tests/test_documents.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/admin-api/src/knowledge_admin/documentation_folders.py services/admin-api/src/knowledge_admin/api.py \
  services/admin-api/tests/test_documentation_folders.py
git commit -m "feat(admin-api): browse documentation folders and their documents

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: admin-ui — the Code project choice, its form and its history

**Files:**
- Modify: `services/admin-ui/app/lib/documents.ts`
- Create: `services/admin-ui/app/components/CodeProjectDocumentationForm.tsx`
- Modify: `services/admin-ui/app/components/DocumentationWorkflowView.tsx` (`NewDocumentationWorkflow`, `DocumentationWorkflowsView`)
- Modify: `services/admin-ui/app/components/DocumentationGenerationsView.tsx`
- Modify: `services/admin-ui/app/components/DocumentationTokenUsage.tsx` (label for `project`)
- Create: `services/admin-ui/app/components/__tests__/codeProjectDocumentation.test.tsx`

**Interfaces:**
- Consumes: Task 4's input and history fields; Task 6's `GET /api/documentation/folders`.
- Produces, in `lib/documents.ts`: `SAME_DOCUMENT_NAME`, `FOLDER_RULE`, `folderError`,
  `documentSlug`, `defaultFolder`, `CodeProjectGenerationInput`,
  `generateDocumentation(value: GenerationInput | CodeProjectGenerationInput)`, folder types
  (`DocumentationFolder`, `DocumentationFolders`, `FolderDocument`) and
  `getDocumentationFolders`, `getDocumentationFolder` (Task 9 uses them);
  `DocumentationGenerationsView({ workflowId?, scope?, title? })`.

- [ ] **Step 1: Write the failing tests**

Create `services/admin-ui/app/components/__tests__/codeProjectDocumentation.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, expect, test, vi } from 'vitest';
vi.mock('../CodeGraphExplorer', () => ({ default: () => <div>Graph inspection</div> }));
import { DocumentationWorkflowsView, NewDocumentationWorkflow } from '../DocumentationWorkflowView';
import { FOLDER_RULE, SAME_DOCUMENT_NAME } from '~/lib/documents';

const prompts = [
  { id: 'code-prompt', name: 'Code analysis and architecture', input_kind: 'code' },
  { id: 'mixed-prompt', name: 'Environment architecture', input_kind: 'mixed' },
  { id: 'server-prompt', name: 'Deployment and service operations', input_kind: 'server' },
].map(p => ({ ...p, enabled: true, active_version_id: p.id + '-v1' }));
afterEach(() => vi.unstubAllGlobals());

function setup({ graphReady = false, sameName = false } = {}) {
  const posts: Record<string, unknown>[] = [];
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    const json = (status: number, body: unknown) => ({ ok: status < 300, status, json: async () => body });
    if (url === '/api/code-projects/repo/process' && init?.method === 'POST') { graphReady = true; return json(202, { job_id: 'graph' }); }
    if (url === '/api/documentation/generations' && init?.method === 'POST') {
      const body = JSON.parse(init.body as string);
      posts.push(body);
      return sameName && !body.update_existing ? json(409, { error: { message: SAME_DOCUMENT_NAME } }) : json(202, { job_id: 'job' });
    }
    const prompt = prompts.find(p => url === '/api/documentation/prompts/' + p.id);
    if (prompt) return json(200, { ...prompt, versions: [{ id: prompt.active_version_id, version: 3, content: 'Active ' + prompt.name }] });
    const bodies: Record<string, unknown> = {
      '/api/services': { services: [] }, '/api/environments': { environments: [] },
      '/api/llm': { models: [{ id: 'model', name: 'Documentation model', provider: 'openai', api_format: 'responses', enabled: true }] },
      '/api/code-projects': { projects: [{ id: 'repo', name: 'Payments API', code_index: graphReady
        ? { source_hash: 'graph-hash', status: 'ready', stats: { nodeCount: 2, edgeCount: 1 }, unresolved_references: 0 } : null }] },
      '/api/documentation/prompts': { prompts },
      '/api/documentation/folders': { generated_folder: 'generated-documents', folders: [
        { folder: 'payments', source_path: 'generated-documents/payments', document_count: 1, updated_at: '2026-10-07T10:00:00Z',
          last_generated_at: null, code_projects: ['Payments API'] }] },
    };
    return json(200, bodies[url] ?? { generations: [], workflows: [] });
  });
  vi.stubGlobal('fetch', fetch);
  render(<MemoryRouter initialEntries={['/documentation/generations/new']}><Routes>
    <Route path="/documentation/generations/new" element={<NewDocumentationWorkflow />} />
    <Route path="/documentation/generations" element={<div>Generation history page</div>} />
  </Routes></MemoryRouter>);
  return { fetch, posts };
}
async function select(label: string, option: string) {
  fireEvent.click(await screen.findByLabelText(label));
  fireEvent.click(await screen.findByRole('option', { name: option }));
}

test('code project is a third choice that generates one document into a folder after Phase 1', async () => {
  // The folder defaults to the project's slug plus -docs and stays editable.
  const { posts } = setup();
  await select('Scope', 'Code project');
  expect(screen.queryByRole('button', { name: 'Create workflow' })).toBeNull();
  await select('Code project', 'Payments API');
  expect(screen.getByLabelText('Documentation folder')).toHaveValue('payments-api-docs');
  await select('Prompt', 'Code analysis and architecture');
  fireEvent.change(screen.getByLabelText('Document name'), { target: { value: 'Payments – API reference' } });
  fireEvent.change(screen.getByLabelText('Comments'), { target: { value: 'Focus on refunds.' } });
  fireEvent.change(screen.getByLabelText('Documentation folder'), { target: { value: 'team/payments' } });
  expect(screen.getByRole('button', { name: 'Generate' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Generate graph' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Generate' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
  expect(await screen.findByText('Generation history page')).toBeInTheDocument();
  expect(posts).toEqual([{ scope: 'code_project', model_id: 'model', code_project_id: 'repo', prompt_id: 'code-prompt',
    prompt_version_id: 'code-prompt-v1', code_graph_hashes: { repo: 'graph-hash' }, comments: 'Focus on refunds.',
    document_name: 'Payments – API reference', folder: 'team/payments', update_existing: false }]);
});

test('server-only prompts are hidden because the run has no server access', async () => {
  setup({ graphReady: true });
  await select('Scope', 'Code project');
  fireEvent.click(await screen.findByLabelText('Prompt'));
  expect(await screen.findByRole('option', { name: 'Code analysis and architecture' })).toBeInTheDocument();
  expect(screen.getByRole('option', { name: 'Environment architecture' })).toBeInTheDocument();
  expect(screen.queryByRole('option', { name: 'Deployment and service operations' })).toBeNull();
});

test('a bad folder names the rule and keeps Generate disabled', async () => {
  setup({ graphReady: true });
  await select('Scope', 'Code project');
  await select('Code project', 'Payments API');
  await select('Prompt', 'Code analysis and architecture');
  fireEvent.change(screen.getByLabelText('Document name'), { target: { value: 'API reference' } });
  await waitFor(() => expect(screen.getByRole('button', { name: 'Generate' })).toBeEnabled());
  fireEvent.change(screen.getByLabelText('Documentation folder'), { target: { value: '../payments' } });
  expect(screen.getByText(FOLDER_RULE)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Generate' })).toBeDisabled();
});

test('the same name in the same folder asks before saving a new version', async () => {
  const { posts } = setup({ graphReady: true, sameName: true });
  await select('Scope', 'Code project');
  await select('Code project', 'Payments API');
  await select('Prompt', 'Code analysis and architecture');
  fireEvent.change(screen.getByLabelText('Document name'), { target: { value: 'Payments – API reference' } });
  await waitFor(() => expect(screen.getByRole('button', { name: 'Generate' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
  expect(await screen.findByText('Save a new version?')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Save new version' }));
  expect(await screen.findByText('Generation history page')).toBeInTheDocument();
  expect(posts.map(p => p.update_existing)).toEqual([false, true]);
  expect(posts[1]).toMatchObject({ folder: 'payments-api-docs', document_name: 'Payments – API reference' });
});

test('the generate documents page lists code project runs with their document name and folder', async () => {
  const fetch = vi.fn(async (url: string) => ({ ok: true, status: 200, json: async () => url.startsWith('/api/documentation/workflows')
    ? { workflows: [] }
    : { generations: [{ job_id: 'job', scope: 'code_project', document_name: 'Payments – API reference', folder: 'team/payments',
        mode: 'create', instructions: 'Focus on refunds.', status: 'succeeded', error: null, created_at: '2026-10-07T10:00:00Z',
        document_count: 1, token_usage: [] }] } }));
  vi.stubGlobal('fetch', fetch);
  render(<MemoryRouter><DocumentationWorkflowsView /></MemoryRouter>);
  expect(await screen.findByText('Payments – API reference · team/payments')).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'Code project documents' })).toBeInTheDocument();
  expect(fetch.mock.calls.some(([url]) => String(url).includes('scope=code_project'))).toBe(true);
});
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-ui && pnpm exec tsc --noEmit; pnpm test -- codeProjectDocumentation`
Expected: FAIL. `tsc` reports that `FOLDER_RULE` and `SAME_DOCUMENT_NAME` are not exported;
the tests find no **Code project** option.

- [ ] **Step 3: `lib/documents.ts`**

Add `folder?: string | null;` to `DocumentSummary`; add
`scope?: string; document_name?: string | null; folder?: string | null;` to
`DocumentationGeneration` (`GenerationInput` stays the service/environment shape), and add:

```ts
export const SAME_DOCUMENT_NAME = 'A document with this name already exists in this folder. Confirm to save a new version.';
export const FOLDER_RULE = "A documentation folder is a path such as payments or team/payments: each segment uses letters, digits, '.', '_' and '-' and does not start with '.'; node_modules and a first segment services are not allowed; at most 200 characters.";
const FOLDER_SEGMENT = /^[A-Za-z0-9_-][A-Za-z0-9._-]*$/;
/** The server's documentation folder rule: '' when valid, else the rule text. */
export function folderError(folder: string) {
  const parts = folder.split('/');
  return folder.length > 200 || parts.some(part => !FOLDER_SEGMENT.test(part) || part === 'node_modules') || parts[0] === 'services' ? FOLDER_RULE : '';
}
export const documentSlug = (name: string) => name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 120).replace(/-+$/, '');
/** The default documentation folder for a code project: its name as a slug plus -docs ("Payments API" → "payments-api-docs"). */
export function defaultFolder(name: string) {
  const base = name.toLowerCase().replace(/[^a-z0-9._-]+/g, '-').replace(/^[-.]+|-+$/g, '').slice(0, 195).replace(/-+$/, '');
  return base ? `${base}-docs` : 'docs';
}

export interface CodeProjectGenerationInput {
  scope: 'code_project'; model_id: string; code_project_id: string; prompt_id: string; prompt_version_id: string;
  code_graph_hashes: Record<string, string>; comments: string; document_name: string; folder: string; update_existing: boolean;
}
export interface DocumentationFolder { folder: string; document_count: number; updated_at: string; last_generated_at: string | null; code_projects: string[]; source_path: string | null }
export interface DocumentationFolders { generated_folder: string | null; folders: DocumentationFolder[] }
export interface FolderDocument { id: string; slug: string; title: string; version: number; updated_at: string; last_generated_at: string | null; code_projects: string[] }
const folderUrl = (folder: string) => `/api/documentation/folders/${folder.split('/').map(encodeURIComponent).join('/')}`;
export const getDocumentationFolders = (signal?: AbortSignal) => request<DocumentationFolders>('/api/documentation/folders', { signal });
export const getDocumentationFolder = (folder: string, signal?: AbortSignal) => request<{ folder: string; documents: FolderDocument[] }>(folderUrl(folder), { signal });
```

Change `generateDocumentation`'s parameter to `value: GenerationInput | CodeProjectGenerationInput`,
and `getDocumentationGenerations`' filter to
`{ serviceId?: string; kind?: string; workflowId?: string; scope?: string }` with
`if (filter?.scope) params.set('scope', filter.scope);`.

- [ ] **Step 4: Create `components/CodeProjectDocumentationForm.tsx`**

```tsx
import { useEffect, useRef, useState } from 'react';
import { ApiError } from '~/lib/api';
import { getCodeProjects, type CodeProject } from '~/lib/codeProjects';
import { defaultFolder, documentSlug, folderError, generateDocumentation, getDocumentationFolders, getDocumentationPrompt,
  getDocumentationPrompts, SAME_DOCUMENT_NAME, type DocumentationPrompt } from '~/lib/documents';
import CodeGraphPhase from './CodeGraphPhase';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle } from '~/components/ui/alert-dialog';
import { Button } from '~/components/ui/button';
import { Field, FieldDescription, FieldLabel } from '~/components/ui/field';
import { Input } from '~/components/ui/input';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '~/components/ui/select';
import { Skeleton } from '~/components/ui/skeleton';
import { Textarea } from '~/components/ui/textarea';

type Prompt = { id: string; versionId: string; version: number; error?: string };

/** One code project, a code or mixed prompt plus comments, one document saved into a documentation folder. */
export default function CodeProjectDocumentationForm({ modelId, onQueued }: { modelId: string; onQueued: () => void }) {
  const [data, setData] = useState<{ projects: CodeProject[]; prompts: DocumentationPrompt[]; folders: string[] } | null>(null);
  const [projectId, setProjectId] = useState('');
  const [prompt, setPrompt] = useState<Prompt | null>(null);
  const [comments, setComments] = useState('');
  const [name, setName] = useState('');
  const [folder, setFolder] = useState('');
  const [folderTouched, setFolderTouched] = useState(false);
  const [graphHashes, setGraphHashes] = useState<Record<string, string>>({});
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const promptRequest = useRef(0);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getCodeProjects(), getDocumentationPrompts(), getDocumentationFolders()]).then(([projects, prompts, folders]) => {
      if (cancelled) return;
      setData({ projects: projects.projects, folders: folders.folders.map(f => f.folder),
        // The run has no server access: server-only prompts are not offered.
        prompts: prompts.prompts.filter(p => p.enabled && p.active_version_id && (p.input_kind === 'code' || p.input_kind === 'mixed')) });
    }).catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : 'Could not load code projects and prompts'); });
    return () => { cancelled = true; };
  }, []);

  async function selectPrompt(id: string) {
    const request = ++promptRequest.current;
    setPrompt(id ? { id, versionId: '', version: 0 } : null);
    if (!id) return;
    try {
      const detail = await getDocumentationPrompt(id);
      const active = detail.versions.find(v => v.id === detail.active_version_id);
      if (!detail.enabled || !active) throw new Error('This prompt has no active version. Select another prompt.');
      if (promptRequest.current === request) setPrompt({ id, versionId: active.id, version: active.version });
    } catch (e) {
      if (promptRequest.current === request) setPrompt({ id, versionId: '', version: 0, error: e instanceof Error ? e.message : 'Could not load the active prompt' });
    }
  }
  function selectProject(id: string) {
    setProjectId(id);
    const project = data?.projects.find(p => p.id === id);
    if (project && !folderTouched) setFolder(defaultFolder(project.name));
  }
  const badFolder = folder ? folderError(folder) : '';
  const ready = Boolean(modelId && projectId && graphHashes[projectId] && prompt?.versionId && documentSlug(name) && folder && !badFolder);

  async function submit(updateExisting: boolean) {
    if (!ready || pending || !prompt) return;
    setPending(true); setError('');
    try {
      await generateDocumentation({ scope: 'code_project', model_id: modelId, code_project_id: projectId, prompt_id: prompt.id,
        prompt_version_id: prompt.versionId, code_graph_hashes: { [projectId]: graphHashes[projectId] }, comments,
        document_name: name.trim(), folder, update_existing: updateExisting });
      setConfirming(false);
      onQueued();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && e.message === SAME_DOCUMENT_NAME && !updateExisting) setConfirming(true);
      else { setConfirming(false); setError(e instanceof Error ? e.message : 'Could not start generation'); }
    } finally { setPending(false); }
  }
  function choose(label: string, id: string, current: string, items: { id: string; name: string }[], change: (id: string) => void) {
    return <Field><FieldLabel htmlFor={id}>{label}</FieldLabel><Select value={current || 'none'} onValueChange={v => change(v === 'none' ? '' : v)}><SelectTrigger id={id}><SelectValue /></SelectTrigger><SelectContent><SelectGroup><SelectItem value="none">Select…</SelectItem>{items.map(item => <SelectItem key={item.id} value={item.id}>{item.name}</SelectItem>)}</SelectGroup></SelectContent></Select></Field>;
  }
  if (!data) return error ? <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert> : <Skeleton className="h-48 w-full" />;
  return <form className="flex flex-col gap-5" onSubmit={e => { e.preventDefault(); void submit(false); }}>
    <fieldset disabled={pending} className="flex min-w-0 flex-col gap-5">
      {choose('Code project', 'project-doc-project', projectId, data.projects, selectProject)}
      {projectId && <CodeGraphPhase projectIds={[projectId]} onReady={setGraphHashes} />}
      {choose('Prompt', 'project-doc-prompt', prompt?.id ?? '', data.prompts, id => void selectPrompt(id))}
      <FieldDescription>Prompts for code or mixed input. Server prompts are not offered: this run reads code only, without server access, RAG or MCP tools.</FieldDescription>
      {prompt?.error && <Alert variant="destructive"><AlertDescription>{prompt.error} <Button type="button" variant="link" onClick={() => void selectPrompt(prompt.id)}>Reload prompt</Button></AlertDescription></Alert>}
      <Field><FieldLabel htmlFor="project-doc-comments">Comments</FieldLabel><Textarea id="project-doc-comments" rows={4} maxLength={20000} value={comments} onChange={e => setComments(e.target.value)} placeholder="Extra instructions for this run, appended to the prompt" /></Field>
      <Field><FieldLabel htmlFor="project-doc-name">Document name</FieldLabel><Input id="project-doc-name" maxLength={240} value={name} onChange={e => setName(e.target.value)} placeholder="Payments – API reference" />{name && !documentSlug(name) && <FieldDescription className="text-destructive">Use letters or digits in the name.</FieldDescription>}</Field>
      <Field><FieldLabel htmlFor="project-doc-folder">Documentation folder</FieldLabel><Input id="project-doc-folder" list="documentation-folders" maxLength={200} value={folder} onChange={e => { setFolderTouched(true); setFolder(e.target.value.trim()); }} placeholder="payments or team/payments" />
        <datalist id="documentation-folders">{data.folders.map(f => <option key={f} value={f} />)}</datalist>
        <FieldDescription className={badFolder ? 'text-destructive' : undefined}>{badFolder || `Saved as ${folder || '<folder>'}/${documentSlug(name) || '<document>'}/README.md in the generated documents folder. The default is the project name plus -docs; pick an existing folder or type a new one. Make it searchable in RAG → Collections.`}</FieldDescription></Field>
      {projectId && !graphHashes[projectId] && <FieldDescription>Generate the code graph before generating the document.</FieldDescription>}
    </fieldset>
    {error && <Alert variant="destructive"><AlertDescription>{error}{error.includes('Reload the prompt') && prompt && <Button type="button" variant="link" onClick={() => void selectPrompt(prompt.id)}>Reload prompt</Button>}</AlertDescription></Alert>}
    <Button type="submit" className="self-start" disabled={pending || !ready}>{pending ? 'Queuing…' : 'Generate'}</Button>
    <AlertDialog open={confirming} onOpenChange={setConfirming}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>Save a new version?</AlertDialogTitle>
      <AlertDialogDescription>{`${folder} already has a document named “${name.trim()}”. Generating saves a new version of it; earlier versions stay available.`}</AlertDialogDescription></AlertDialogHeader>
      <AlertDialogFooter><AlertDialogCancel disabled={pending}>Cancel</AlertDialogCancel><AlertDialogAction disabled={pending} onClick={e => { e.preventDefault(); void submit(true); }}>Save new version</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
  </form>;
}
```

- [ ] **Step 5: The New page and the history**

In `components/DocumentationWorkflowView.tsx`, import
`CodeProjectDocumentationForm from './CodeProjectDocumentationForm'`, and in
`NewDocumentationWorkflow`:
- `const [scope,setScope]=useState<'service'|'environment'|'code_project'>('service');`
- the submit handler starts with `if (scope === 'code_project') return;` and passes
  `scope: scope as 'service' | 'environment'` to `createDocumentationWorkflow`;
- the scope choices become
  `[{id:'service',name:'Service'},{id:'environment',name:'Whole environment'},{id:'code_project',name:'Code project'}]`;
- the subject select renders only when `scope!=='code_project'`; the **Create workflow**
  button too;
- the intro paragraph reads `Generate one document for a code project with a prompt you choose and save it into a documentation folder.`
  for `code_project`, and keeps its current text otherwise;
- wrap the returned `<form>` in `<div className="flex max-w-2xl flex-col gap-5">…</div>` (move
  `max-w-2xl` from the form to the div) and, after the form, render
  `{scope==='code_project' && data && <CodeProjectDocumentationForm modelId={model} onQueued={() => navigate('/documentation/generations')} />}`.

In `DocumentationWorkflowsView`, after the paging buttons, add
`<DocumentationGenerationsView scope="code_project" title="Code project documents" />`.

In `components/DocumentationGenerationsView.tsx`:
- props `({ workflowId, scope, title = 'Generation history' }: { workflowId?: string; scope?: 'code_project'; title?: string })`;
- the request passes `{workflowId, scope}` and the effect depends on `[offset, refresh, workflowId, scope]`;
- the heading renders `{title}`;
- the Request cell starts with
  `{row.scope === 'code_project' && <p className="font-medium">{`${row.document_name} · ${row.folder}`}</p>}`.

In `components/DocumentationTokenUsage.tsx`, add `project: 'Code project document'` to
`names`, so the run's usage row is labelled.

- [ ] **Step 6: Run the tests and confirm they pass**

Run: `cd services/admin-ui && pnpm exec tsc --noEmit && pnpm test -- codeProjectDocumentation documentationGeneration`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add services/admin-ui/app/lib/documents.ts services/admin-ui/app/components/CodeProjectDocumentationForm.tsx \
  services/admin-ui/app/components/DocumentationWorkflowView.tsx services/admin-ui/app/components/DocumentationGenerationsView.tsx \
  services/admin-ui/app/components/DocumentationTokenUsage.tsx services/admin-ui/app/components/__tests__/codeProjectDocumentation.test.tsx
git commit -m "feat(admin-ui): New documentation for a code project with a chosen prompt, comments and folder

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: admin-ui — RAG New collection with folders, synced right away; `?folder=` prefill

**Files:**
- Modify: `services/admin-ui/app/components/RagFolderPicker.tsx` (extract `FolderBrowser`)
- Modify: `services/admin-ui/app/components/RagCollectionForm.tsx`
- Modify: `services/admin-ui/app/components/RagCollectionsView.tsx`
- Modify: `services/admin-ui/app/components/RagCollectionDetail.tsx`
- Modify: `services/admin-ui/app/lib/rag.ts` (add `collectionNameFor`)
- Modify: `services/admin-ui/app/components/__tests__/ragCollections.test.tsx`

**Interfaces:**
- Consumes: RAG's existing `POST collections`, `POST collections/{id}/paths`,
  `POST collections/{id}/sync` through `/api/rag/…` (`createCollection`, `addFolder`,
  `syncCollection` in `lib/rag.ts`). The RAG service and admin-api do not change.
- Produces: `FolderBrowser({ value, onChange })` (exported from `RagFolderPicker.tsx`);
  `RagCollectionForm` props `initialName?: string`, `initialFolders?: string[]`,
  `onSaved(saved: { id: string }, problem?: string)`; `collectionNameFor(folder: string): string`;
  `/rag/collections?folder=<path>` opens New collection with that folder selected and the
  name prefilled; `RagCollectionDetail` shows `location.state.folderError`.

- [ ] **Step 1: Write the failing tests**

In `services/admin-ui/app/components/__tests__/ragCollections.test.tsx`, add `useLocation`
to the `react-router` import and append:

```tsx
function Opened() {
  const location = useLocation();
  return <div>detail page {(location.state as { folderError?: string } | null)?.folderError ?? ""}</div>;
}
function creating(failPaths = false) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.startsWith("/api/rag/folders")) return Response.json({ path: null, truncated: false,
      folders: url === "/api/rag/folders" ? [{ name: "jira", path: "jira" }] : [] });
    if (init?.method === "POST" && url === "/api/rag/collections") return Response.json({ ...base, id: "c9", name: "payments-api-docs", paths: [] }, { status: 201 });
    if (init?.method === "POST" && url.endsWith("/paths")) return failPaths
      ? Response.json({ error: { message: "Folder does not exist" } }, { status: 409 })
      : Response.json({ ...base, id: "c9", paths: [] });
    if (init?.method === "POST" && url.endsWith("/sync")) return Response.json({ id: "job" }, { status: 202 });
    return Response.json({ collections: [] });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}
function collectionsAt(entry: string) {
  render(<MemoryRouter initialEntries={[entry]}><Routes>
    <Route path="/rag/collections" element={<RagCollectionsView />} />
    <Route path="/rag/collections/:id" element={<Opened />} /></Routes></MemoryRouter>);
}
const writes = (fetchMock: ReturnType<typeof creating>) => fetchMock.mock.calls
  .filter(([, init]) => init?.method && init.method !== "GET")
  .map(([url, init]) => [init!.method, url, JSON.parse(init!.body as string)]);

test("New collection with folders connects each folder and queues a sync right away", async () => {
  const fetchMock = creating();
  const user = userEvent.setup();
  collectionsAt("/rag/collections");
  await user.click(await screen.findByRole("button", { name: "New collection" }));
  await user.type(screen.getByLabelText("Name"), "payments-api-docs");
  await user.click(screen.getByRole("button", { name: "Choose a folder" }));
  await user.click(await screen.findByRole("button", { name: "jira" }));
  await user.click(screen.getByRole("button", { name: "Add to selection" }));
  await user.click(screen.getByRole("button", { name: "Choose a folder" }));
  await user.type(screen.getByLabelText("Folder path"), "generated-documents/payments-api-docs");
  await user.click(screen.getByRole("button", { name: "Add to selection" }));
  await user.click(screen.getByRole("button", { name: "Remove jira" }));
  await user.click(screen.getByRole("button", { name: "Choose a folder" }));
  await user.click(await screen.findByRole("button", { name: "jira" }));
  await user.click(screen.getByRole("button", { name: "Add to selection" }));
  await user.click(screen.getByRole("button", { name: "Create collection" }));
  expect(await screen.findByText("detail page")).toBeInTheDocument();
  expect(writes(fetchMock).map(([method, url, body]) => url === "/api/rag/collections" ? [method, url, body.name] : [method, url, body])).toEqual([
    ["POST", "/api/rag/collections", "payments-api-docs"],
    ["POST", "/api/rag/collections/c9/paths", { source_path: "generated-documents/payments-api-docs" }],
    ["POST", "/api/rag/collections/c9/paths", { source_path: "jira" }],
    ["POST", "/api/rag/collections/c9/sync", { force: false }]]);
});

test("?folder= opens New collection with that folder selected and a name from it", async () => {
  const fetchMock = creating();
  collectionsAt("/rag/collections?folder=generated-documents%2Fpayments-api-docs");
  expect(await screen.findByLabelText("Name")).toHaveValue("payments-api-docs");
  expect(screen.getByText("generated-documents/payments-api-docs")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Remove generated-documents/payments-api-docs" })).toBeInTheDocument();
  expect(writes(fetchMock)).toEqual([]);
});

test("a folder that cannot be added keeps the collection and opens it with the error", async () => {
  const fetchMock = creating(true);
  const user = userEvent.setup();
  collectionsAt("/rag/collections?folder=generated-documents%2Fpayments-api-docs");
  await user.click(await screen.findByRole("button", { name: "Create collection" }));
  expect(await screen.findByText(/Collection payments-api-docs was created, but folder generated-documents\/payments-api-docs could not be added: Folder does not exist\. Fix its folders here\./)).toBeInTheDocument();
  expect(writes(fetchMock).map(([method, url]) => [method, url])).toEqual([
    ["POST", "/api/rag/collections"], ["POST", "/api/rag/collections/c9/paths"]]);  // no sync, nothing deleted
});

test("a collection opened after a failed folder shows why", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => Response.json(url.includes("/log") ? noLog : base)));
  render(<MemoryRouter initialEntries={[{ pathname: "/rag/collections/c1", state: { folderError: "Collection jira was created, but folder x could not be added: Folder does not exist. Fix its folders here." } }]}><Routes>
    <Route path="/rag/collections/:id" element={<RagCollectionDetail id="c1" />} /></Routes></MemoryRouter>);
  expect(await screen.findByText(/folder x could not be added/)).toBeInTheDocument();
});
```

The existing tests "New collection creates a collection and opens it" and "an invalid
name disables Create collection" stay unchanged: with no folder selected, Create
collection sends only `POST collections` and never browses `DATA_ROOT`.

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-ui && pnpm exec tsc --noEmit; pnpm test -- ragCollections ragFolderPicker`
Expected: the four new tests FAIL (no **Choose a folder** button, no prefill, no
`folderError` alert); the existing tests pass.

- [ ] **Step 3: Extract `FolderBrowser` in `components/RagFolderPicker.tsx`**

Move the browsing state, the listing effect, the browsing list and the **Folder path**
field out of `Picker` into an exported component, unchanged in markup and labels:

```tsx
/** Browse DATA_ROOT or type a folder path; `value` is the typed or browsed path, relative to DATA_ROOT. */
export function FolderBrowser({ value, onChange }: { value: string; onChange: (path: string) => void }) {
  const [browsing, setBrowsing] = useState<string | null>(null);
  const [listing, setListing] = useState<FolderListing | null>(null);
  const [browseError, setBrowseError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setListing(null); setBrowseError("");
    getFolders(browsing, controller.signal)
      .then((result) => { if (!controller.signal.aborted) setListing(result); })
      .catch((e) => { if (!controller.signal.aborted) setBrowseError(e instanceof Error ? e.message : "Could not list folders"); });
    return () => controller.abort();
  }, [browsing]);
  function browse(path: string | null) { setBrowsing(path); onChange(path ?? ""); }
  return <>
    {/* the existing "Browsing … / Up / folder list / truncated" block, unchanged */}
    <Field><FieldLabel htmlFor="folder-path">Folder path</FieldLabel>
      <Input id="folder-path" value={value} placeholder="code/payments" onChange={(e) => onChange(e.target.value)} />
      <FieldDescription>Relative to DATA_ROOT, such as jira or code/payments.</FieldDescription></Field>
  </>;
}
```

`Picker` keeps `value`, `error`, `busy` and `submit`, and renders
`<FolderBrowser value={value} onChange={setValue} />` between its error alert and the
**Connect folder** button. `ragFolderPicker.test.tsx` passes unchanged.

- [ ] **Step 4: Folders in `components/RagCollectionForm.tsx`**

- Props: add `initialName?: string; initialFolders?: string[];` and change
  `onSaved` to `(saved: { id: string }, problem?: string) => void`; pass the two initial
  values to `SettingsFields`.
- `SheetDescription` for a new collection: `Name the collection and choose its search settings. Optionally select folders: they are connected and synced right away.`
- In `SettingsFields`: `const [name, setName] = useState(initialName ?? "");`,
  `const [folders, setFolders] = useState<string[]>(initialFolders ?? []);`,
  `const [choosing, setChoosing] = useState(false);`, `const [folderValue, setFolderValue] = useState("");`.
- Replace `submit`'s `try` block:

```tsx
    try {
      if (collection) { onSaved(await saveCollectionSettings(collection.id, { ...value, revision: collection.revision })); return; }
      const created = await createCollection({ name, ...value });
      let step = "";
      try {
        for (const folder of folders) { step = `folder ${folder} could not be added`; await addFolder(created.id, folder); }
        if (folders.length) { step = "its sync could not be queued"; await syncCollection(created.id); }
      } catch (e) {
        // The collection exists: keep it and open it, so the admin fixes its folders there.
        onSaved(created, `Collection ${name} was created, but ${step}: ${e instanceof Error ? e.message : "request failed"}. Fix its folders here.`);
        return;
      }
      onSaved(created);
    } catch (e) {
```

  (the existing `catch` stays).
- Before the **Create collection** button, for a new collection only:

```tsx
      {!collection && <FieldSet><FieldLegend>Folders</FieldLegend>
        <FieldDescription>Optional. Each folder is connected to the new collection and a sync is queued at once. Folders must exist under DATA_ROOT.</FieldDescription>
        {folders.length > 0 && <ul className="flex flex-col gap-1">{folders.map((folder) => <li key={folder} className="flex items-center gap-2 text-sm">
          <code className="break-all">{folder}</code>
          <Button type="button" variant="ghost" size="sm" aria-label={`Remove ${folder}`} onClick={() => setFolders((current) => current.filter((f) => f !== folder))}>Remove</Button></li>)}</ul>}
        {choosing ? <>
          <FolderBrowser value={folderValue} onChange={setFolderValue} />
          <Button type="button" variant="outline" className="self-start" disabled={normalizeFolder(folderValue) === "" || folders.includes(normalizeFolder(folderValue))}
            onClick={() => { setFolders((current) => [...current, normalizeFolder(folderValue)]); setFolderValue(""); setChoosing(false); }}>Add to selection</Button>
        </> : <Button type="button" variant="outline" className="self-start" onClick={() => setChoosing(true)}>Choose a folder</Button>}
      </FieldSet>}
```

  Imports: `addFolder`, `normalizeFolder`, `syncCollection` from `../lib/rag`;
  `FieldLegend`, `FieldSet` from `~/components/ui/field`; `FolderBrowser` from `./RagFolderPicker`.

- [ ] **Step 5: The prefill in `components/RagCollectionsView.tsx` and the error in `RagCollectionDetail.tsx`**

In `lib/rag.ts`, add:

```ts
/** A collection name from a folder's last segment: lowercase letters, digits and single hyphens, at most 60 characters. */
export const collectionNameFor = (folder: string) => (normalizeFolder(folder).split("/").pop() ?? "").toLowerCase()
  .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60).replace(/-+$/, "");
```

In `RagCollectionsView`:
- `const prefill = params.get("folder");` and `const [creating, setCreating] = useState(prefill !== null);`;
- the form becomes:

```tsx
    <RagCollectionForm open={creating} onOpenChange={setCreating}
      initialName={prefill ? collectionNameFor(prefill) : ""} initialFolders={prefill ? [normalizeFolder(prefill)] : []}
      onSaved={(saved, problem) => { setCreating(false); navigate(`/rag/collections/${saved.id}`, problem ? { state: { folderError: problem } } : undefined); }} />
```

  (import `collectionNameFor`, `normalizeFolder`); the empty-table text becomes
  `No collections yet. Choose New collection and select its folders.`

In `RagCollectionDetail`, import `useLocation` and add
`const location = useLocation();` and
`const [folderError] = useState(() => (location.state as { folderError?: string } | null)?.folderError ?? "");`;
render `{folderError && <Alert variant="destructive"><AlertDescription>{folderError}</AlertDescription></Alert>}`
directly above the existing error alert. It is never cleared by `load`.

- [ ] **Step 6: Run the tests and confirm they pass**

Run: `cd services/admin-ui && pnpm exec tsc --noEmit && pnpm test -- ragCollections ragFolderPicker`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add services/admin-ui/app/components/RagFolderPicker.tsx services/admin-ui/app/components/RagCollectionForm.tsx \
  services/admin-ui/app/components/RagCollectionsView.tsx services/admin-ui/app/components/RagCollectionDetail.tsx \
  services/admin-ui/app/lib/rag.ts services/admin-ui/app/components/__tests__/ragCollections.test.tsx
git commit -m "feat(admin-ui): New collection connects selected folders and syncs them right away

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: admin-ui — Docs → Folders browse page

**Files:**
- Create: `services/admin-ui/app/components/DocumentationFoldersView.tsx`
- Create: `services/admin-ui/app/routes/documentationFolders.tsx`
- Create: `services/admin-ui/app/routes/documentationFolder.tsx`
- Modify: `services/admin-ui/app/routes.ts`
- Modify: `services/admin-ui/app/components/AdminLayout.tsx`
- Create: `services/admin-ui/app/components/__tests__/documentationFolders.test.tsx`
- Modify: `services/admin-ui/app/components/__tests__/adminLayout.test.tsx`

**Interfaces:**
- Consumes: Task 7's `getDocumentationFolders`, `getDocumentationFolder` and folder types;
  `getCollectionNames` (`GET /api/rag/collections?status=false`); Task 8's `?folder=` prefill.
- Produces: `DocumentationFoldersView` (default export) and `DocumentationFolderView({ folder })`;
  routes `/documentation/folders` and `/documentation/folders/*`.

- [ ] **Step 1: Write the failing tests**

Create `services/admin-ui/app/components/__tests__/documentationFolders.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, expect, test, vi } from 'vitest';
import DocumentationFoldersView, { DocumentationFolderView } from '../DocumentationFoldersView';

afterEach(() => vi.unstubAllGlobals());
const folders = { generated_folder: 'generated-documents', folders: [
  { folder: 'payments', document_count: 2, updated_at: '2026-10-07T10:00:00Z', last_generated_at: '2026-10-07T10:00:00Z',
    code_projects: ['Payments API'], source_path: 'generated-documents/payments' },
  { folder: 'team/payments', document_count: 1, updated_at: '2026-10-07T10:00:00Z', last_generated_at: null,
    code_projects: [], source_path: 'generated-documents/team/payments' }] };
const collections = { collections: [
  { id: 'c1', name: 'payments-docs', paths: [{ id: 'p1', source_path: 'generated-documents/payments' }] },
  { id: 'c2', name: 'jira', paths: [{ id: 'p2', source_path: 'jira' }] },
  { id: 'c3', name: 'all-docs', paths: [{ id: 'p3', source_path: 'generated-documents' }] },
  { id: 'c4', name: 'lookalike', paths: [{ id: 'p4', source_path: 'generated-documents/pay' }] }] };

function setup(rag: () => Response = () => Response.json(collections)) {
  const fetch = vi.fn(async (url: string) => url === '/api/rag/collections?status=false' ? rag() : Response.json(folders));
  vi.stubGlobal('fetch', fetch);
  render(<MemoryRouter><DocumentationFoldersView /></MemoryRouter>);
  return fetch;
}

test('folders show their documents, projects and the RAG collections that include them, read-only', async () => {
  const fetch = setup();
  expect(await screen.findByRole('link', { name: 'team/payments' })).toHaveAttribute('href', '/documentation/folders/team/payments');
  expect(screen.getByText('Payments API')).toBeInTheDocument();
  expect(await screen.findByRole('link', { name: 'payments-docs' })).toHaveAttribute('href', '/rag/collections/c1');
  expect(screen.getAllByRole('link', { name: 'all-docs' })).toHaveLength(2);
  expect(screen.getAllByText('through generated-documents')).toHaveLength(2);
  expect(screen.queryByRole('link', { name: 'jira' })).toBeNull();
  expect(screen.queryByRole('link', { name: 'lookalike' })).toBeNull();
  expect(screen.getByRole('link', { name: 'Index team/payments in RAG → Collections' }))
    .toHaveAttribute('href', '/rag/collections?folder=generated-documents%2Fteam%2Fpayments');
  expect(screen.queryByRole('button', { name: /Disconnect|Index/ })).toBeNull();
  expect(fetch.mock.calls.every(([, init]) => !(init as RequestInit | undefined)?.method || (init as RequestInit).method === 'GET')).toBe(true);
});

test('folders still list when RAG is unavailable', async () => {
  setup(() => Response.json({ error: { message: 'RAG service is unavailable' } }, { status: 502 }));
  expect(await screen.findByRole('link', { name: 'payments' })).toBeInTheDocument();
  expect(await screen.findByText('RAG collections unavailable: RAG service is unavailable')).toBeInTheDocument();
});

test('opening a folder lists its documents linking to Documents', async () => {
  const fetch = vi.fn(async (_url: string) => Response.json({ folder: 'team/payments', documents: [{ id: 'd1', slug: 'api-reference',
    title: 'Payments – API reference', version: 2, updated_at: '2026-10-07T10:00:00Z', last_generated_at: '2026-10-07T10:00:00Z',
    code_projects: ['Payments API'] }] }));
  vi.stubGlobal('fetch', fetch);
  render(<MemoryRouter><DocumentationFolderView folder="team/payments" /></MemoryRouter>);
  expect(await screen.findByRole('link', { name: 'Payments – API reference' })).toHaveAttribute('href', '/documents/d1');
  expect(screen.getByText('Payments API')).toBeInTheDocument();
  expect(fetch.mock.calls[0][0]).toBe('/api/documentation/folders/team/payments');
});
```

In `components/__tests__/adminLayout.test.tsx`, after the **Generate documents**
assertion, add:

```tsx
  expect(docs.getByRole("link", { name: "Folders" })).toHaveAttribute("href", "/documentation/folders");
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-ui && pnpm exec tsc --noEmit; pnpm test -- documentationFolders adminLayout`
Expected: FAIL: `../DocumentationFoldersView` does not exist and there is no **Folders**
link.

- [ ] **Step 3: Create `components/DocumentationFoldersView.tsx`**

```tsx
import { useEffect, useState } from 'react';
import { Link } from 'react-router';
import { getDocumentationFolder, getDocumentationFolders, type DocumentationFolders, type FolderDocument } from '~/lib/documents';
import { getCollectionNames, type CollectionSummary } from '~/lib/rag';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Badge } from '~/components/ui/badge';
import { Skeleton } from '~/components/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '~/components/ui/table';

const folderLink = (folder: string) => `/documentation/folders/${folder.split('/').map(encodeURIComponent).join('/')}`;
const message = (e: unknown, fallback: string) => e instanceof Error ? e.message : fallback;
const when = (value: string | null) => value ? new Date(value).toLocaleString() : '—';

/** Collections whose folders include `source`: the folder itself, or a parent folder ("through" it). */
function including(collections: Pick<CollectionSummary, 'id' | 'name' | 'paths'>[], source: string) {
  return collections.flatMap(c => c.paths.filter(p => p.source_path === source || source.startsWith(p.source_path + '/'))
    .map(p => ({ id: c.id, name: c.name, through: p.source_path === source ? null : p.source_path })));
}

export default function DocumentationFoldersView() {
  const [data, setData] = useState<DocumentationFolders | null>(null);
  const [collections, setCollections] = useState<CollectionSummary[] | null>(null);
  const [ragError, setRagError] = useState('');
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    getDocumentationFolders(controller.signal).then(result => { if (!controller.signal.aborted) setData(result); })
      .catch(e => { if (!controller.signal.aborted) setError(message(e, 'Could not load documentation folders')); });
    // Read-only RAG state: which collections include each folder. Indexing happens in RAG → Collections.
    getCollectionNames(controller.signal).then(result => { if (!controller.signal.aborted) setCollections(result.collections); })
      .catch(e => { if (!controller.signal.aborted) setRagError(message(e, 'RAG is unavailable')); });
    return () => controller.abort();
  }, []);
  return <div className="flex flex-col gap-5">
    <div><h1 className="text-2xl font-semibold">Documentation folders</h1><p className="text-muted-foreground">Folders in the generated documents folder{data?.generated_folder ? ` (${data.generated_folder})` : ''}. To make a folder searchable, create a collection with it in RAG → Collections.</p></div>
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    {ragError && <Alert><AlertDescription>{`RAG collections unavailable: ${ragError}`}</AlertDescription></Alert>}
    {data === null ? (!error && <Skeleton className="h-40" />) : data.folders.length === 0
      ? <p>No documentation folders yet. Generate a code project document into a folder in Docs → Generate documents.</p>
      : <Table><TableHeader><TableRow><TableHead>Folder</TableHead><TableHead>Documents</TableHead><TableHead>Code projects</TableHead><TableHead>Last generated</TableHead><TableHead>RAG collections</TableHead><TableHead>Index</TableHead></TableRow></TableHeader><TableBody>
        {data.folders.map(row => {
          const found = row.source_path && collections ? including(collections, row.source_path) : [];
          return <TableRow key={row.folder}>
            <TableCell><Link className="underline" to={folderLink(row.folder)}>{row.folder}</Link></TableCell>
            <TableCell>{row.document_count}</TableCell>
            <TableCell>{row.code_projects.join(', ') || '—'}</TableCell>
            <TableCell>{when(row.last_generated_at)}</TableCell>
            <TableCell>{collections === null ? (ragError ? '—' : 'Loading…') : found.length === 0 ? <Badge variant="outline">Not indexed</Badge>
              : <ul className="flex flex-col gap-1">{found.map(c => <li key={`${c.id}:${c.through ?? ''}`} className="text-sm">
                  <Link className="underline" to={`/rag/collections/${c.id}`}>{c.name}</Link>
                  {c.through && <span className="ml-2 text-xs text-muted-foreground">{`through ${c.through}`}</span>}</li>)}</ul>}</TableCell>
            <TableCell>{row.source_path ? <Link className="underline" aria-label={`Index ${row.folder} in RAG → Collections`}
              to={`/rag/collections?folder=${encodeURIComponent(row.source_path)}`}>Index in RAG → Collections</Link> : '—'}</TableCell>
          </TableRow>;
        })}
      </TableBody></Table>}
  </div>;
}

export function DocumentationFolderView({ folder }: { folder: string }) {
  const [rows, setRows] = useState<FolderDocument[] | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    setRows(null); setError('');
    getDocumentationFolder(folder, controller.signal).then(result => { if (!controller.signal.aborted) setRows(result.documents); })
      .catch(e => { if (!controller.signal.aborted) setError(message(e, 'Could not open the folder')); });
    return () => controller.abort();
  }, [folder]);
  return <div className="flex flex-col gap-5"><Link className="underline" to="/documentation/folders">Documentation folders</Link><h1 className="text-2xl font-semibold">{folder}</h1>
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    {rows === null ? (!error && <Skeleton className="h-40" />) : <Table><TableHeader><TableRow><TableHead>Document</TableHead><TableHead>Code project</TableHead><TableHead>Last generated</TableHead><TableHead>Version</TableHead></TableRow></TableHeader><TableBody>
      {rows.map(d => <TableRow key={d.id}><TableCell><Link className="underline" to={`/documents/${d.id}`}>{d.title}</Link></TableCell><TableCell>{d.code_projects.join(', ') || '—'}</TableCell><TableCell>{when(d.last_generated_at)}</TableCell><TableCell>v{d.version}</TableCell></TableRow>)}
    </TableBody></Table>}
  </div>;
}
```

- [ ] **Step 4: Routes and navigation**

`routes/documentationFolders.tsx`:

```tsx
import AdminLayout from '../components/AdminLayout';
import DocumentationFoldersView from '../components/DocumentationFoldersView';

export default function Page() {
  return <AdminLayout><DocumentationFoldersView /></AdminLayout>;
}
```

`routes/documentationFolder.tsx`:

```tsx
import { useParams } from 'react-router';
import AdminLayout from '../components/AdminLayout';
import { DocumentationFolderView } from '../components/DocumentationFoldersView';

export default function Page() {
  const folder = useParams()['*'] ?? '';
  return <AdminLayout><DocumentationFolderView folder={folder} /></AdminLayout>;
}
```

In `routes.ts`, after the `documentation/generations/:workflowId` route, add:

```ts
  route("documentation/folders", "./routes/documentationFolders.tsx"),
  route("documentation/folders/*", "./routes/documentationFolder.tsx"),
```

In `components/AdminLayout.tsx`:
- add `FolderOpen` to the `lucide-react` import;
- add `const docsFolders = pathname.startsWith('/documentation/folders');` after `docsHistory`;
- the docs title becomes
  `(docsPrompts ? "Documentation prompts" : docsHistory ? "Generate documentation" : docsFolders ? "Documentation folders" : "Documents")`;
- after the **Generate documents** menu item, add:

```tsx
          <SidebarMenuItem><SidebarMenuButton asChild isActive={docsFolders}><Link to="/documentation/folders"><FolderOpen /><span>Folders</span></Link></SidebarMenuButton></SidebarMenuItem>
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `cd services/admin-ui && pnpm exec tsc --noEmit && pnpm test`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/admin-ui/app/components/DocumentationFoldersView.tsx services/admin-ui/app/routes/documentationFolders.tsx \
  services/admin-ui/app/routes/documentationFolder.tsx services/admin-ui/app/routes.ts services/admin-ui/app/components/AdminLayout.tsx \
  services/admin-ui/app/components/__tests__/documentationFolders.test.tsx services/admin-ui/app/components/__tests__/adminLayout.test.tsx
git commit -m "feat(admin-ui): Docs → Folders browses folders, their documents and the collections that include them

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: User guide and full verification

**Files:**
- Modify: `docs/user-guide.md`

**Interfaces:**
- Consumes: the behaviour of Tasks 1–9. The guide repeats the Global Constraints' rule
  text, paths and messages exactly.

- [ ] **Step 1: Write the docs**

`docs/user-guide.md`:
- **§5.1 Navigation**: the Docs row becomes `Documents · Prompts · Generate documents · Folders`.
- **§5.3 RAG**: Workflow step 1 becomes **New collection** → name, description,
  settings and, optionally, **Folders** (**Choose a folder**, browse or type, **Add to
  selection**; several allowed) → **Create collection** connects them and queues a sync
  at once. If a folder cannot be added, the collection is kept and opened with the error;
  fix its folders there. `?folder=<path>` (used by Docs → Folders) opens New collection
  with that folder selected. Steps 2–3 (Add folder, Refresh) stay for existing
  collections.
- **§5.13 Documentation generation**: in the intro, workflows are for services and
  environments, and code projects get a single document without a workflow. Under
  **Create a workflow**, mention the third scope. Add a subsection **Code project
  documents** with:
  - the steps: **New documentation** → scope **Code project** → model → **Code project**
    (Phase 1 **Generate graph** appears; **Generate** stays disabled until the project has a
    graph) → **Prompt** (active prompts for code or mixed input; server prompts are hidden
    because the run has no server access) → **Comments** (optional, up to 20,000
    characters, appended to the prompt for this run) → **Document name** →
    **Documentation folder** (default: the project name plus `-docs`, for example
    `payments-api-docs`; pick an existing folder or type a new one) → **Generate**;
  - the folder rule text from the Global Constraints;
  - what the agent gets: that project's code and graph tools; no SSH, RAG or MCP;
  - the result: a document in **Docs → Documents** (versions; linked to the code
    project; prompt, model and source commits recorded with the run), exported to
    `GENERATED_DOCS_FOLDER/<folder>/<document-slug>/README.md` with `.d2` diagrams and
    `metadata.json`; the run listed under **Code project documents** on the Generate
    documents page;
  - the same name in the same folder asks **Save a new version?**; the same name in
    another folder is a separate document;
  - errors: "The prompt changed. Reload the prompt before generating." (use **Reload
    prompt**), "Source or graph selection changed…" (run **Generate graph** again), and a
    folder whose first part is already a document's export folder (choose another folder).
- **§5.14 Documents and documentation prompts**: add **Folders**
  (`/documentation/folders`): the folders with their document count, code projects and
  last generated time, and read-only the RAG collections that include each one (**Not
  indexed**, or each collection, "through <folder>" when it holds a parent folder), each
  linking to its collection; **Index in RAG → Collections** opens RAG's New collection
  with the folder selected. Opening a folder lists its documents (title, code project,
  last generated), each opening in Documents. Editing a folder document keeps it in its
  folder.
- **§6**: add **G. Make one code project's documentation searchable**: Generate documents →
  New documentation → Code project → … → Generate; Docs → Folders → **Index in RAG →
  Collections** → Create collection (the folder is selected, the sync starts at once);
  grant the collection to agents in Agents.

- [ ] **Step 2: Run every suite**

Run:
```bash
cd services/admin-api && uv run --frozen pytest -q
cd ../rag && uv run --frozen pytest -q
cd ../admin-ui && pnpm exec tsc --noEmit && pnpm test
cd ../.. && python3 -m unittest discover -s tests && bash db/tests/run.sh
```
Expected: everything passes. For any failure, check out `main` in a separate worktree
and run the same test. Accept it only if it also fails there, and record it in the PR
description. Never run `make setup`, `make compose-validate`, `make up` or `make smoke`.

- [ ] **Step 3: Check for leftovers**

Run: `git grep -n "documents_project_id_slug_key" -- db services`
Expected: only `db/migrations/0068_document_folders.up.sql` and `.down.sql`.

Run: `git grep -n "d.slug=%s" -- services/admin-api/src`
Expected: every match also filters the folder (`d.folder IS NULL` or `d.folder=%s`).

Run: `git diff main --stat -- services/rag`
Expected: no changes.

- [ ] **Step 4: Commit**

```bash
git add docs/user-guide.md
git commit -m "docs: code project documentation into named folders and folder indexing in RAG

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```
