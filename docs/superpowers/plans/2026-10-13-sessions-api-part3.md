# One Sessions API, Part 3 (Writing a New Document with an Agent) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new document is an editable resource of an ordinary agent run, written with `save_document`; people start it from Documents → New in the shared chat; the Generate documents form, API and final-answer rule go.

**Architecture:** `document_access` entries gain a `new:` kind handled inside `capabilities/document_tools.py`; `sessions/start.py` accepts `started_from.new_document` and `resources.documents[{new}]` and records purpose `documents`. Migration 0101 seeds "Write a document" and closes open Generate documents runs. The UI adds a `new-document` chat scope used on the New document page.

**Tech Stack:** FastAPI + psycopg 3 (pytest with a throwaway Postgres), golang-migrate SQL, React Router 7 + shadcn + vitest.

**Spec:** `docs/superpowers/specs/2026-10-13-sessions-api-part3-design.md`

## Global Constraints

- Work only in the worktree `.claude/worktrees/sessions-part3` (branch `feature/sessions-part3`). Never run `make` targets (that includes `db/tests/run.sh`), never touch `.env` or `.local`, never `git stash`, never touch the running Docker stack or its database.
- Backend tests: `cd services/admin-api && uv run --frozen pytest -q <files>`. Known failures to ignore: code-graph tests in worktrees (incl. `test_code_processing::test_go_module_root_…`, `test_code_graphs_config::test_a_generated_graph_answers_code_queries_end_to_end`), `test_deploy::test_docs_commands_serve_the_docs_folder_inside_the_data_root`, `test_environments::test_migration_reapply_and_populated_rollback_protection`.
- UI: `cd services/admin-ui && pnpm exec vitest run`, `pnpm exec tsc --noEmit`, `pnpm exec react-router routes`. Once, from the worktree root: `ln -s ../../../../../services/admin-ui/node_modules services/admin-ui/node_modules` (git-ignored; never commit it; never `pnpm install`). `skills.test.tsx` can flake under load — rerun alone.
- Texts, verbatim: `Not written yet.`; `Write the document with save_document first.`; `Closed by an update; start a new chat.`; `Give the document a title and folder first.`; `Writing: {folder}/{title}`; `Choose one starting point: a document or a new document.`; refusal `This document is not available in this run.` (unchanged).
- Behaviour comes from the prompt: code appends only the start's `context` under `# Context`; the `FINAL_ANSWER` instruction is deleted.
- The narrow-only guarantee for saved agents and Ask agent behaviour stay as they are.
- Commit after each task; messages end with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Never `git add` `.superpowers` or `node_modules`.

## Review Focus

1. A `new:` id from another run (or a made-up one) must never create or write a draft — Task 1 pins it.
2. The title and folder of an existing document must make the agent's saves a draft of that document, and later saves must keep updating that same draft — Task 1 pins it.
3. Every test that used `POST /api/generations/sessions` as a way to get a run must still run after the route goes — Task 2 keeps them via the rewritten `start()` helper.
4. An open Generate documents run from before must not keep running with the final-answer rule — Task 2's migration test pins its closing.
5. Ask agent on an existing document must look and behave exactly as before after the new-document scope is added — Task 3 keeps every pane test.

---

### Task 1: A new document as a resource; new-document starts

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/capabilities/document_tools.py`
- Modify: `services/admin-api/src/knowledge_admin/sessions/start.py` (`DocumentAccess`, `StartedFrom`, `document_access`, `build`, `options`)
- Modify: `services/admin-api/src/knowledge_admin/sessions/api.py` (start route insert; options query `new_document`)
- Test: `services/admin-api/tests/test_new_document_runs.py` (create)

**Interfaces:**
- `DocumentAccess` becomes `{id: UUID | None, new: NewDocument | None, editable: bool}` with `NewDocument = {folder: str, name: str}`; exactly one of `id` / `new` (validator: `Give a document id or a new document.`); at most one `new` entry per start (`One new document per run.`); a `new` entry is always editable.
- `StartedFrom` becomes `{document_id: UUID | None, new_document: NewDocument | None}`, exactly one (`Choose one starting point: a document or a new document.`). `NewDocument` validates `name` with `document_slug` (`Use letters or digits in the document name`) and normalises `folder` with `documentation_folder` (its own messages).
- Pinned entry for a new document: `{'id': f'new:{uuid4()}', 'title': name, 'folder': folder, 'slug': document_slug(name), 'editable': True, 'new': True}`. In `build()`, the started-from new document and the `resources.documents` new entry must be the same `{folder, name}`; if `started_from.new_document` is given and no `new` resource is, the start adds it.
- Purpose and columns: started from a new document → purpose `documents`, `folder`, `document_name`, `target_document_id` NULL, `context_snapshot = {'new_document': {'folder', 'name'}}`.
- `GET /api/sessions/options?new_document=1`: as for an ad-hoc start with no document (models, prompts tagged `documentation` instead of `document-revision`, default prompt id `md5('knowledge-agent-v3:prompts:write-document:' || project)` when present else the first, all resources unlinked, tools, experts, `documents: []`). With `document_id` or `agent_id` as well: 400 `Choose one starting point: a document or a new document.`
- `document_tools`: for an id starting with `new:` —
  - `allowed(config, 'document', id, write=…)` as for any id (the pinned entry is the allowlist);
  - `get_document`: the run's open, unedited draft that has `session_id` = the run and `document_id IS NULL` with the entry's folder and slug, or (when an existing document matched) the run's open draft whose `document_id` is that document — see `_new_document_draft()` below; none → `Not written yet.`;
  - `save_document`: first save → `create_draft(conn, project, None, content, origin='agent', slug=entry['slug'], folder=entry['folder'], session_id=…)` with `content = {**NEW_DEFAULTS, 'title': title or entry['title'], 'description': description or '', 'markdown': markdown}` (`NEW_DEFAULTS` from `document_drafts`); later saves update that draft (as for existing documents); after a person edits, promotes or discards it, the next save creates a new one (today's notes);
  - `edit_document` with no draft yet → `Write the document with save_document first.`;
  - sources are recorded with `record_run_sources(conn, project, draft_id, draft['document_id'], draft['base_version_id'], session, turns)`.
  - `_new_document_draft(conn, session_id, entry, lock)`: `SELECT … FROM ka.document_drafts WHERE session_id=%s AND status='open' AND NOT edited AND ((document_id IS NULL AND folder IS NOT DISTINCT FROM %s AND slug=%s) OR document_id=(SELECT id FROM ka.documents WHERE project_id=%s AND folder IS NOT DISTINCT FROM %s AND slug=%s)) ORDER BY created_at DESC,id DESC LIMIT 1` (+ `FOR UPDATE` when locking).

- [ ] **Step 1: Write the failing tests** (`tests/test_new_document_runs.py`; reuse the `sessions` fixture from `test_generations` and the document tool call pattern from `tests/document_tools_world.py`):

```python
"""Writing a new document with an agent: a new document is a resource the run may edit; the agent writes it with
save_document; the first save creates the new-document draft."""
import asyncio, json
from uuid import uuid4

import pytest

from knowledge_admin.capabilities.context import NOT_AVAILABLE
from knowledge_admin.tools.catalog import CATALOG
from test_generations import sessions  # noqa: F401  (fixture)
from test_repositories import HEADERS

NEW = {'folder': 'payments-api-docs', 'name': 'Payments – API reference'}


def body(s, **change):
    return {'started_from': {'new_document': NEW}, 'message': 'Write the API reference.', 'model_id': s.gpt,
            'system_prompt': 'You document systems.', 'resources': {'code_project_ids': [s.repo], 'documents': [{'new': NEW, 'editable': True}]},
            'tools': ['read_file', 'get_document', 'save_document', 'edit_document'], **change}


def call(config, tool, **arguments):
    spec = CATALOG[tool]
    return asyncio.run(spec.run({'config': config}, spec.arguments.model_validate(arguments)))


def test_a_new_document_start_pins_a_new_entry_and_records_purpose_documents(sessions):
    started = sessions.client.post('/api/sessions', headers=HEADERS, json=body(sessions))
    assert started.status_code == 201, started.text
    session = started.json()
    assert (session['purpose'], session['folder'], session['document_name'], session['target_document_id']) == (
        'documents', 'payments-api-docs', 'Payments – API reference', None)
    [entry] = session['config']['document_access']
    assert entry['id'].startswith('new:') and entry['editable'] and entry['new'] and entry['slug'] == 'payments-api-reference'


def test_the_first_save_creates_the_new_document_draft_and_later_saves_update_it(sessions, monkeypatch):
    ...  # start a run; build config = {**session['config'], 'session_id': session['id']}; monkeypatch inventory._store
         # to sessions.store (as document_tools_world does); get_document on the new id says 'Not written yet.';
         # edit_document raises 'Write the document with save_document first.'; save_document creates one draft with
         # document_id None, folder and slug as pinned, origin 'agent', session_id the run; a second save updates the
         # same draft (revision 2); get_document then reads it ('reads': 'your draft').


def test_an_existing_documents_title_and_folder_make_the_saves_a_draft_of_it(sessions, monkeypatch):
    ...  # save a live document with folder payments-api-docs and slug payments-api-reference first; the run's first
         # save creates a draft whose document_id is that document; a second save updates the same draft.


def test_no_other_new_id_can_be_written(sessions, monkeypatch):
    ...  # with the pinned config, save_document on 'new:' + str(uuid4()) and on another run's new id raises NOT_AVAILABLE
         # for kind document; no draft is created.


@pytest.mark.parametrize('change,message', [
    ({'started_from': {'new_document': NEW, 'document_id': str(uuid4())}}, 'Choose one starting point: a document or a new document.'),
    ({'started_from': {'new_document': {**NEW, 'name': '!!!'}}}, 'Use letters or digits in the document name'),
])
def test_bad_new_document_starts_are_refused(sessions, change, message):
    response = sessions.client.post('/api/sessions', headers=HEADERS, json=body(sessions, **change))
    assert response.status_code == 400 and message in response.json()['error']['message']


def test_options_for_a_new_document(sessions):
    options = sessions.client.get('/api/sessions/options?new_document=1', headers=HEADERS).json()
    assert options['resources']['documents'] == [] and all(not item['linked'] for item in options['resources']['services'])
    assert {'save_document', 'edit_document', 'get_document'} <= {tool['name'] for tool in options['tools']}
```

Write the three `...` bodies fully, following the comments (assert exact draft rows from `ka.document_drafts`).

- [ ] **Step 2: Run them to verify they fail** — `uv run --frozen pytest -q tests/test_new_document_runs.py` (FAIL: `started_from` has no `new_document`).
- [ ] **Step 3: Implement** as in **Interfaces**. Keep the `allowed()` check first in every save. The seeded-prompt default id uses `write-document` (Task 2 seeds it; until then options fall back to the first prompt).
- [ ] **Step 4: Run** `tests/test_new_document_runs.py tests/test_document_edit_tools.py tests/test_sessions_start.py tests/test_sessions_saved_agent.py` — PASS.
- [ ] **Step 5: Commit** `feat(agents): a new document is a resource a run may write with save_document`.

---

### Task 2: The final-answer rule and the Generate documents API go; migration 0101

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/session_text.py` (delete `FINAL_ANSWER`), `knowledge_agents/session_worker.py` (the documents branch), `document_drafts.py` (delete `save_run_draft`, `save_run_draft_safely` and helpers only they use, e.g. `run_resources` if unused elsewhere), `api.py` (drop `generations_api`)
- Delete: `services/admin-api/src/knowledge_admin/generations_api.py`, `generate_documents.py`, `tests/test_generate_documents.py`
- Create: `db/migrations/0101_write_documents.up.sql`, `.down.sql`, `tests/test_write_documents_migration.py`
- Modify: `tests/test_generations.py` (the `start`/`refused` helpers; delete tests of the removed form API), `tests/test_document_drafts_worker.py` (delete final-answer draft tests), `tests/session_rows.py` and any other user of `/api/generations`; `db/tests/run.sh`; `docs/migrations.md`

**Interfaces:**
- `tests/test_generations.py`: `start(sessions, **change)` and `refused(sessions, **change)` keep their names and the `sessions.body` keys (`model_id`, the five selection id lists, `system_prompt`, `prompt`, `document_name`, `folder`, `prompt_id`), and translate to `POST /api/sessions`: `{'started_from': {'new_document': {'folder', 'name': document_name}}, 'message': prompt, 'model_id', 'system_prompt', 'prompt_id', 'resources': {<selection lists>, 'documents': [{'new': …, 'editable': True}]}, 'tools': <every tool the options list for this selection>}`. Callers stay unchanged. Delete the tests of the form API itself (context endpoint, FINAL_ANSWER, the form's own validation) and keep every test about run operations.
- Worker: after a turn, only `refresh_run_sources_safely` runs (the `draft` column and `draft_turn` are no longer written).
- Migration 0101 (copy 0100's structure): seed function `seed_write_document_prompt(uuid)` + trigger `seed_write_document` on projects + seed every project; prompt id `md5('knowledge-agent-v3:prompts:write-document:' || workspace)`, name `Write a document`, tags `{documentation,new-document}`, `input_kind 'mixed'`, text:

```
You write one technical document from the code, documents and servers you were given. Read them before you write, and check facts against them. Never include passwords, keys or tokens.

Rules:
- Write the complete document with save_document; change a passage with edit_document. Each save updates your draft; a person reviews it and decides whether it becomes a document. Never say it is published.
- Read what you wrote with get_document.
- When the request is unclear, ask in plain chat before you write.
- When you are done, say in a few sentences what you wrote.
```

  then discard queued inputs of and close open `documents` runs with `Closed by an update; start a new chat.`. Down: the customized guard (`'The Write a document prompt has been customized; preserve it before rollback'`), delete, drop trigger and functions.
- `run.sh`: one more `down 1` step first (check `to_regprocedure('ka.seed_write_document_prompt(uuid)') IS NULL`) and one more up check (`… IS NOT NULL`, `PASS: the Write a document prompt is seeded.`). Do not run it.

- [ ] **Step 1: Write the failing migration test** (as `tests/test_sessions_api_migration.py` does for 0100: the prompt is seeded in a new workspace with the rule words `save_document`, `edit_document`, `get_document`; open `documents` runs are closed with the message and their queued inputs discarded while a `revision` run stays open; down refuses a customized prompt, then round-trips).
- [ ] **Step 2: Run it to verify it fails**, then write the migration; **run** it — PASS.
- [ ] **Step 3: Remove the API and the final-answer rule** as in **Interfaces**; rewrite the test helpers; `grep -rn "generations_api\|generate_documents\|FINAL_ANSWER\|save_run_draft\|/api/generations" services/admin-api` must show nothing but deliberate 404 assertions (add one: `POST /api/generations/sessions` and `/api/generations/context` answer 404/405).
- [ ] **Step 4: Run** the full backend suite — PASS apart from the known failures.
- [ ] **Step 5: Document** 0101 in `docs/migrations.md` (seeded prompt; open Generate documents runs closed; custom `documentation` prompts should tell the agent to write with `save_document`).
- [ ] **Step 6: Commit** `feat(agents): documents are written with save_document; the Generate documents API and the final-answer rule go (0101)`.

---

### Task 3: "Write with an agent" on the New document page

**Files:**
- Modify: `services/admin-ui/app/lib/sessions.ts` (`ChatScope`, `StartInput`, `getSessionOptions`), `lib/agentSettings.ts`, `components/AgentChat.tsx`, `components/AgentSettingsSheet.tsx`, `components/DocumentEditorView.tsx`
- Test: `lib/__tests__/agentSettings.test.ts`, `components/__tests__/documentNewAgent.test.tsx` (create); the existing pane tests stay green

**Interfaces:**
- `ChatScope` adds `{ kind: 'new-document'; folder: string; title: string }`.
- `getSessionOptions({ newDocument: true })` → `/api/sessions/options?new_document=1`.
- `StartInput.started_from` and `resources.documents` entries accept `{ new_document: { folder, name } }` / `{ new: { folder, name }, editable: true }`.
- `agentSettings`: storage key `agent-settings:new-document`; options loaded once per page (`new-document` key, not per title); `startInput` for the scope → `{ started_from: { new_document: { folder, name: title } }, message, model_id, prompt_id, system_prompt, context: '', resources: { ...chosen, documents: [{ new: { folder, name: title }, editable: true }] }, tools, expert_ids, subagents }`.
- `AgentChat` for `new-document`: region label `Ask agent`; no run list (no `getSessions`; Earlier runs hidden); New chat clears; heading `Writing: {folder}/{title}` once a run is open (the run's own `folder`/`document_name`); before a run, the composer is blocked with `Give the document a title and folder first.` when either is empty; draft callbacks as for documents; foreign check off.
- `DocumentEditorView`: on the New page (no `documentId`), a header button **Write with an agent** (`aria-pressed`) toggles the pane in the same 2:1 full-width layout; the editor reports its Title and Folder through a new `onFieldsChange(title, folder)` prop; the pane gets `scope={{ kind: 'new-document', title, folder }}`; the draft bar shows the run's draft; its Promote navigates to `/documents/{result.document_id}`.
- `AgentSettingsSheet`: for `new-document` the description is `What the agent may use for “{title}”.`; no Documents resource group (the new document is implied).

- [ ] **Step 1: Write the failing tests** — in `agentSettings.test.ts`: the new-document `startInput` body (exact object); in `documentNewAgent.test.tsx` (route `/documents/new` with `DocumentEditorView`, fetch stubs as in `documentAgentPane.test.tsx`): the button opens the pane; with an empty Title the composer shows `Give the document a title and folder first.`; after typing Title `Payments API` and Folder `payments`, sending posts the body with `started_from.new_document` `{ folder: 'payments', name: 'Payments API' }`; with a session whose `document_draft` is open, the draft bar shows; Promote (stub `POST /api/documentation/drafts/{id}/promote` → `{ document_id: 'doc-9', … }`) navigates to `/documents/doc-9`.
- [ ] **Step 2: Run them to verify they fail**, **Step 3: implement**, **Step 4: run** the full UI suite, `tsc` and the route check (PASS; the existing pane tests unchanged), **Step 5: commit** `feat(admin-ui): write a new document with an agent from Documents → New`.

---

### Task 4: The Generate documents pages go; the run page shows any run's draft

**Files:**
- Delete: `services/admin-ui/app/components/DocumentGenerationForm.tsx`, `GenerationsView.tsx`, `lib/generations.ts`, `routes/documentationGenerations.tsx`, `routes/documentationGenerationNew.tsx` and their tests (`documentGenerationForm.test.tsx`, `generations.test.tsx`)
- Modify: `app/routes.ts`, `components/AdminLayout.tsx` (sidebar: drop Generate documents; `docsHistory` and any `documentation/generations` matching), `components/SessionView.tsx`, any link to `/documentation/generations` (e.g. SessionView's "Started from" fallback for `documents` runs → link to the draft or nothing)
- Test: `components/__tests__/sessionView.test.tsx`, `adminLayout.test.tsx`

**Interfaces:**
- `SessionView`: the draft section shows for any run with a `document_draft` (Open draft for open; Open document once promoted, using `document_draft.document_id ?? session.document_id`); a run with a final-answer `draft` text (old runs) still offers Preview draft. "Started from" for a new-document run: `the new document {document_name}` as plain text (no page).
- Sidebar Docs group: Documents, Folders (no Generate documents).

- [ ] **Step 1: Write the failing tests** — SessionView: a `revision` run with an open `document_draft` shows Open draft; a `documents` run without the old `draft` text shows Open draft from `document_draft`; adminLayout: no `Generate documents` link.
- [ ] **Step 2–4:** fail, implement, run the full UI suite, `tsc`, routes — PASS; `grep -rn "documentation/generations\|lib/generations\|DocumentGenerationForm\|GenerationsView" services/admin-ui/app` → nothing.
- [ ] **Step 5: Commit** `feat(admin-ui): the Generate documents pages go; run pages show any run's draft`.

---

### Task 5: Docs and the final check

**Files:** `docs/user-guide.md` (Generate documents section → "Write a new document with an agent" on Documents → New; Agent runs/run page mentions), `docs/documentation.md` (the generation API and form → new-document runs; the "Write a document" prompt; custom prompts should say to write with `save_document`), `docs/agents.md` (new-document resource), `deploy/README.md` (removed routes in the upgrade notes), `README.md` if it names Generate documents.

- [ ] **Step 1: Update the docs** in plain, short sentences; nothing describes the removed form, list, routes or the final-answer rule as current.
- [ ] **Step 2: Final search**

```bash
git grep -n "api/generations\|generations_api\|generate_documents\|FINAL_ANSWER\|save_run_draft\|DocumentGenerationForm\|GenerationsView\|documentation/generations\|lib/generations" -- . ':!docs/superpowers' ':!db/migrations'
```

Expected: only deliberate 404 assertions in tests and upgrade notes naming removed routes (list them in the report).
- [ ] **Step 3: Run everything** (backend full suite, UI checks) — PASS apart from the known failures.
- [ ] **Step 4: Commit** `docs: write a new document with an agent; Generate documents is gone`.
