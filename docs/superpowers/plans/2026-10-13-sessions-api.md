# One Sessions API (part 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One `/api/sessions` API (run operations, list, options, preview, start), document tools as plain allowlisted tools, Ask agent moved onto it, and one run page `/sessions/:id`.

**Architecture:** A new backend package `knowledge_admin/sessions/` takes the generic run routes out of `generations_api.py` and the Ask agent start out of `agent_launch.py` (deleted). What a run may use is pinned in its config at start and checked inside each tool by one helper, `allowed()`. The document tools (`get_document`, `edit_document`, `save_document`) take `document_id`; the run's session id reaches them as `config['session_id']`, set by the worker when it loads a turn. On the UI, `lib/sessions.ts` replaces `agentSessions.ts` and the generic half of `generations.ts`, and `SessionView` (`/sessions/:id`) replaces `GenerationView`.

**Tech Stack:** FastAPI + psycopg 3 (admin-api, pytest with a throwaway Postgres), golang-migrate SQL migrations, React Router 7 + shadcn + vitest (admin-ui, pnpm).

**Spec:** `docs/superpowers/specs/2026-10-13-sessions-api-design.md`

## Global Constraints

- Work only in the worktree `.claude/worktrees/sessions-api` (branch `feature/sessions-api`). Never run `make` targets, never touch `.env` or `.local`, never `git stash`, never touch the running Docker stack or its database.
- Backend tests: `cd services/admin-api && uv run --frozen pytest -q <files>` (they start their own Postgres container). Code-graph tests fail in worktrees (no `services/code-graph/node_modules`); `test_deploy::test_docs_commands_serve_the_docs_folder_inside_the_data_root` and `test_environments::test_migration_reapply_and_populated_rollback_protection` already fail on main. Ignore exactly those.
- UI checks: `cd services/admin-ui && pnpm exec vitest run`, `pnpm exec tsc --noEmit`, `pnpm exec react-router routes`. The worktree has no `node_modules`: once, run `ln -s ../../../../../services/admin-ui/node_modules services/admin-ui/node_modules` from the worktree root (it is git-ignored; never commit it).
- Behaviour comes from the prompt: code appends no instructions. The only text code adds to a system prompt is the start's `context` under `# Context`, and the existing resource listing (facts).
- Refusal texts, verbatim: `This {kind} is not available in this run.` (kind: `server`, `document`); `Saved agents start from their Test tab for now.`; `Choose what the agent starts from.`; `Closed by an update; start a new chat.`; `The document tools work only in an agent session.`
- Part 1 rulings (from the spec's open edges): a start needs `started_from` (the `agent_sessions` constraints need a purpose and a document for an ad-hoc run until later parts add more); the `document_id` enum goes on `edit_document` and `save_document` only (`get_document` also reads inventory documents whose ids come from other tools, so an enum there would block them); a whole-document `get_document` read keeps today's JSON shape (`id`, `title`, `markdown` capped, `truncated`) and adds `reads`, `lines` and `outline`, so discovery keeps working.
- Commit after each task with a message ending in `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Never `git add` `.superpowers` or `node_modules`.

## Review Focus

1. A tool call naming a document the run was not given (or a read-only one for a save) must be refused by the server even when the model ignores the schema enum — Task 2 pins it with a direct tool call outside the enum.
2. A session whose agent writes two different documents gets one draft per document, and each turn's source refresh covers both — Task 3 pins it.
3. An open Ask agent run from before the update must not keep running with `revision_run` and the old tool names — Task 4's migration test pins that 0100 closes it and discards its queued messages.
4. Browser-saved Ask agent settings from before the update (`v: 1`, `default_prompt`, `fixed_tools`) must not crash the pane — Task 8 pins that they are dropped and rebuilt.
5. A run link from anywhere (Generate documents list, a draft, discovery fallback) must reach a page that shows the run — Task 9 pins every link target.

---

### Task 1: One allowlist check, `allowed()`, used by `ssh_run`

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/capabilities/context.py`
- Modify: `services/admin-api/src/knowledge_admin/capabilities/ssh.py:162-171`
- Test: `services/admin-api/tests/test_allowed.py` (create)

**Interfaces:**
- Produces: `allowed(config: dict, kind: str, item_id: str, *, write: bool = False) -> dict` and `NOT_AVAILABLE = 'This {kind} is not available in this run.'` in `knowledge_admin.capabilities.context`. Kinds: `'server'` (entries of `config['ssh']['servers']` when `config['ssh']['enabled']`), `'document'` (entries of `config.get('document_access', [])`, each `{id, title, editable}`).

- [ ] **Step 1: Write the failing test**

```python
"""allowed(): the one rule every per-resource tool uses — the argument must be pinned in the run's config."""
import pytest

from knowledge_admin.capabilities.context import NOT_AVAILABLE, allowed

CONFIG = {'ssh': {'enabled': True, 'servers': [{'id': 's1', 'name': 'web-1'}]},
          'document_access': [{'id': 'd1', 'title': 'Runbook', 'editable': True},
                              {'id': 'd2', 'title': 'Overview', 'editable': False}]}


def test_a_pinned_entry_is_returned():
    assert allowed(CONFIG, 'server', 's1')['name'] == 'web-1'
    assert allowed(CONFIG, 'document', 'd2')['title'] == 'Overview'
    assert allowed(CONFIG, 'document', 'd1', write=True)['editable']


@pytest.mark.parametrize('kind,item,write', [('server', 's9', False), ('document', 'd9', False), ('document', 'd2', True)])
def test_anything_else_is_refused(kind, item, write):
    with pytest.raises(ValueError, match=f'^{NOT_AVAILABLE.format(kind=kind)}$'):
        allowed(CONFIG, kind, item, write=write)


def test_ssh_switched_off_or_no_documents_allow_nothing():
    with pytest.raises(ValueError):
        allowed({**CONFIG, 'ssh': {'enabled': False, 'servers': CONFIG['ssh']['servers']}}, 'server', 's1')
    with pytest.raises(ValueError):
        allowed({'ssh': {'enabled': False, 'servers': []}}, 'document', 'd1')
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd services/admin-api && uv run --frozen pytest -q tests/test_allowed.py`
Expected: FAIL with `ImportError: cannot import name 'NOT_AVAILABLE'`.

- [ ] **Step 3: Implement `allowed()`**

Append to `capabilities/context.py`:

```python
NOT_AVAILABLE = 'This {kind} is not available in this run.'
ACCESS = {
    'server': lambda config: config['ssh']['servers'] if config['ssh']['enabled'] else [],
    'document': lambda config: config.get('document_access', []),
}


def allowed(config: dict[str, Any], kind: str, item_id: str, *, write: bool = False) -> dict[str, Any]:
    """The pinned entry of one resource this run may use: the run's allowlist, fixed when it started. Every tool that
    takes a resource id checks it here; anything else — and, with write, an entry that is not editable — is refused."""
    entry = next((item for item in ACCESS[kind](config) if str(item['id']) == str(item_id)), None)
    if entry is None or (write and not entry.get('editable')):
        raise ValueError(NOT_AVAILABLE.format(kind=kind))
    return entry
```

In `capabilities/ssh.py`, `ssh_run`, replace

```python
    server = next((item for item in config['ssh']['servers'] if item['id'] == server_id), None)
    if server is None or not server['enabled'] or not server['ssh_allowed']:
        raise ValueError('This server is not available to this agent.')
```

with

```python
    server = allowed(config, 'server', server_id)
    if not server['enabled'] or not server['ssh_allowed']:
        raise ValueError(NOT_AVAILABLE.format(kind='server'))
```

and import `NOT_AVAILABLE, allowed` from `knowledge_admin.capabilities.context` (next to the existing `require_given` import).

- [ ] **Step 4: Update the old message in tests**

Run `grep -rn "not available to this agent" services/admin-api/tests` and replace each occurrence of `'This server is not available to this agent.'` with `'This server is not available in this run.'`.

- [ ] **Step 5: Run the tests**

Run: `cd services/admin-api && uv run --frozen pytest -q tests/test_allowed.py tests/test_ssh.py` (test_ssh starts a throwaway SSH container, as it always does).
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/admin-api/src/knowledge_admin/capabilities/context.py services/admin-api/src/knowledge_admin/capabilities/ssh.py services/admin-api/tests
git commit -m "feat(agents): one allowlist check for per-resource tools; ssh_run uses it"
```

---

### Task 2: Document tools as plain tools taking `document_id`

**Files:**
- Rename: `services/admin-api/src/knowledge_admin/capabilities/revision.py` → `capabilities/document_tools.py` (`git mv`)
- Modify: `services/admin-api/src/knowledge_admin/capabilities/inventory.py` (remove `_document`, `get_document`, `MAX_MARKDOWN`, `NO_DOCUMENT`)
- Modify: `services/admin-api/src/knowledge_admin/tools/catalog.py` (arguments, specs, needs)
- Rename: `services/admin-api/tests/revision_world.py` → `tests/document_tools_world.py`; `tests/test_revision_tools.py` → `tests/test_document_edit_tools.py`
- Modify: `services/admin-api/tests/test_inventory_tools.py`, `tests/test_document_draft_reads.py`

**Interfaces:**
- Consumes: `allowed(config, 'document', id, write=...)` (Task 1).
- Produces, in `knowledge_admin.capabilities.document_tools`: `get_document(context, document_id, section=None, start_line=None, end_line=None)`, `save_document(context, document_id, markdown, title=None, description=None)`, `edit_document(context, document_id, old_text, new_text)`, `outline(markdown)` (unchanged), `MAX_MARKDOWN = 50_000`, `NO_SESSION`. They read `config['session_id']` (a string; set by the worker in Task 3) and `config['project_id']`.
- Produces, in `tools/catalog.py`: specs `get_document` (group `inventory`, needs `None`), `save_document` and `edit_document` (group `documents`, needs `'editable_document'`); `Need` includes `'editable_document'` and drops `'revision'`; `RUN_ONLY_NEEDS = ('discovery', 'editable_document')`; `DOCUMENT_EDIT_TOOLS = ('save_document', 'edit_document')` replaces `REVISION_TOOLS`; `needs_met('editable_document', config, …)` is true when any `document_access` entry is editable; `NEED_REASONS['editable_document'] = 'Needs a document to edit'`.

- [ ] **Step 1: Move the files**

```bash
git mv services/admin-api/src/knowledge_admin/capabilities/revision.py services/admin-api/src/knowledge_admin/capabilities/document_tools.py
git mv services/admin-api/tests/revision_world.py services/admin-api/tests/document_tools_world.py
git mv services/admin-api/tests/test_revision_tools.py services/admin-api/tests/test_document_edit_tools.py
```

In every file that imports them, rename: `capabilities.revision` → `capabilities.document_tools`, `revision_tools` (the catalog's import alias) → `document_tools`, `revision_world` → `document_tools_world`.

- [ ] **Step 2: Rewrite the test fixture for the new config shape**

In `tests/document_tools_world.py`, the `rev` fixture's config becomes:

```python
    config = {'project_id': str(docs.project), 'session_id': str(sid),
              'document_access': [{'id': str(docs.runbook), 'title': 'Runbook', 'editable': True}]}
```

and `call()` stays as it is. In `tests/test_document_edit_tools.py`, rename the tools in every call (`get_doc` → `get_document`, `save_doc` → `save_document`, `edit_doc` → `edit_document`) and pass `document_id=str(rev.docs.runbook)` in each call. Rename tests whose names say `get_doc`/`save_doc`/`edit_doc` to the new names. Replace the three tests about run-only tools and the revision need (`test_the_document_tools_are_run_only_and_kept_from_subagents`, `test_a_subagent_of_a_revision_run_is_not_offered_the_document_tools`, `test_only_an_ask_agent_run_meets_the_revision_need`) with the tests in Step 3. Keep `test_a_revision_turn_never_turns_its_answer_into_a_draft_and_refreshes_sources` for Task 3 (it will be updated there; mark it `@pytest.mark.skip(reason='Task 3')` now).

- [ ] **Step 3: Write the new failing tests** (append to `tests/test_document_edit_tools.py`)

```python
from knowledge_admin.capabilities.context import NOT_AVAILABLE
from knowledge_admin.tools.catalog import CATALOG, DOCUMENT_EDIT_TOOLS, RUN_ONLY_NEEDS, needs_met


def test_edits_and_saves_are_refused_outside_the_runs_documents_even_without_the_enum(rev):
    other = str(uuid4())
    refused = NOT_AVAILABLE.format(kind='document')
    for tool, arguments in (('save_document', {'markdown': '# x'}), ('edit_document', {'old_text': 'Intro.', 'new_text': 'x'})):
        with pytest.raises(ValueError, match=refused):
            call(rev, tool, document_id=other, **arguments)
    read_only = {**rev.config, 'document_access': [{'id': str(rev.docs.runbook), 'title': 'Runbook', 'editable': False}]}
    with pytest.raises(ValueError, match=refused):
        asyncio.run(CATALOG['save_document'].run({'config': read_only},
                                                 CATALOG['save_document'].arguments.model_validate({'document_id': str(rev.docs.runbook), 'markdown': '# x'})))
    assert run_drafts(rev) == []


def test_get_document_reads_the_runs_draft_once_there_is_one(rev):
    whole = json.loads(call(rev, 'get_document', document_id=str(rev.docs.runbook)))
    assert whole['reads'] == 'the live version 2' and whole['markdown'].startswith('# Runbook')
    assert {'line': 5, 'level': 2, 'text': 'Backup'} in whole['outline'] and whole['lines'] == len(RUNBOOK.splitlines())
    call(rev, 'save_document', document_id=str(rev.docs.runbook), markdown='# Runbook\n\nNew text.')
    assert json.loads(call(rev, 'get_document', document_id=str(rev.docs.runbook)))['reads'] == 'your draft'


def test_a_document_neither_in_the_run_nor_in_the_inventory_cannot_be_read(rev):
    assert call(rev, 'get_document', document_id=str(uuid4())) == NOT_AVAILABLE.format(kind='document')
    assert call(rev, 'get_document', document_id='not-an-id') == NOT_AVAILABLE.format(kind='document')


def test_the_edit_tools_need_an_editable_document_and_are_never_chosen_for_saved_agents():
    assert all(CATALOG[name].needs == 'editable_document' for name in DOCUMENT_EDIT_TOOLS)
    assert 'editable_document' in RUN_ONLY_NEEDS and 'revision' not in RUN_ONLY_NEEDS
    assert needs_met('editable_document', {'document_access': [{'id': 'd', 'title': 'T', 'editable': True}]}, None)
    assert not needs_met('editable_document', {'document_access': [{'id': 'd', 'title': 'T', 'editable': False}]}, None)
    assert not needs_met('editable_document', {}, None)
    assert CATALOG['get_document'].needs is None and not {'get_doc', 'save_doc', 'edit_doc'} & set(CATALOG)


def test_without_a_session_the_edit_tools_refuse(rev):
    no_session = {key: value for key, value in rev.config.items() if key != 'session_id'}
    with pytest.raises(ValueError, match='The document tools work only in an agent session.'):
        asyncio.run(CATALOG['save_document'].run({'config': no_session}, CATALOG['save_document'].arguments.model_validate(
            {'document_id': str(rev.docs.runbook), 'markdown': '# x'})))
```

Add `import json` and `from uuid import uuid4` at the top if missing, and import `RUNBOOK` from `document_tools_world`.

- [ ] **Step 4: Run them to verify they fail**

Run: `cd services/admin-api && uv run --frozen pytest -q tests/test_document_edit_tools.py`
Expected: FAIL (`KeyError: 'get_document'` arguments mismatch / `ImportError: DOCUMENT_EDIT_TOOLS`).

- [ ] **Step 5: Rewrite `capabilities/document_tools.py`**

Replace the module docstring, the constants `NOT_REVISION`, the `_run`, `_get_doc`, `_save`, `_save_once` functions and the three async tools as follows (keep `DraftText`, `outline`, `_numbered`, `_outline_text`, `_replace`, `FENCE`, `HEADING`, `LIVE` and the message constants `DOCUMENT_GONE`, `CLOSED`, `NOT_FOUND`, `MANY`, `NO_HEADING`, `PROMOTED`, `DISCARDED`, `EDITED`, `SAVED`, `OUT_OF_RANGE`, `MAX_READ`; in `NOT_FOUND` and `EDITED` replace `get_doc` with `get_document`):

```python
"""The document tools: get_document reads a document the run was given (its own open draft of it when it has one,
else the live version) or one attached to an inventory entry; save_document and edit_document save the run's draft of
a document it was given as editable. Which documents a run may use is pinned in its config (document_access) and
checked here with allowed(); the run's session is config['session_id'], set by the worker. A save locks the run's
session, then its draft (the order the worker and promote take them in), never ka.projects or ka.documents rows, and
recomputes the draft's sources from the run's tool calls."""
import asyncio
import json
import re
from collections.abc import Callable
from typing import Any
from uuid import UUID

from psycopg import errors
from pydantic import BaseModel, ValidationError

from knowledge_admin.capabilities.context import NOT_AVAILABLE, allowed
from knowledge_admin.capabilities.inventory import connect, project_of
from knowledge_admin.document_drafts import audit, create_draft, record_run_sources
from knowledge_admin.documents import Description, Markdown, Title

NO_SESSION = 'The document tools work only in an agent session.'
MAX_MARKDOWN = 50_000
WORKING = '''SELECT id,title,description,purpose,audience,tags,markdown FROM ka.document_drafts
    WHERE session_id=%s AND document_id=%s AND status='open' AND NOT edited ORDER BY created_at DESC,id DESC LIMIT 1'''
ATTACHED = 'SELECT 1 FROM ka.inventory_documents WHERE document_id=%s'


def _document_id(value: str) -> UUID:
    try:
        return UUID(str(value))
    except ValueError:
        raise ValueError(NOT_AVAILABLE.format(kind='document')) from None


def _readable(conn, config: dict[str, Any], document: UUID) -> bool:
    """A document the run was given, or one attached to an inventory entry (read-only, as get_document always allowed)."""
    try:
        allowed(config, 'document', str(document))
        return True
    except ValueError:
        return conn.execute(ATTACHED, (document,)).fetchone() is not None


def _get_document(config: dict[str, Any], document_id: str, section: str | None, start_line: int | None,
                  end_line: int | None) -> str:
    try:
        document = _document_id(document_id)
    except ValueError as exc:
        return str(exc)
    with connect() as conn:
        project = project_of(conn, config)
        live = conn.execute(LIVE, (document, project)).fetchone()
        if live is None or not _readable(conn, config, document):
            return NOT_AVAILABLE.format(kind='document')
        session = config.get('session_id')
        draft = conn.execute(WORKING, (session, document)).fetchone() if session else None
    copy = draft or live
    lines = copy['markdown'].splitlines()
    if section is not None:
        headings = outline(copy['markdown'])
        found = next((h for h in headings if h['text'].strip().lower() == section.strip().lower()), None)
        if found is None:
            return NO_HEADING.format(name=section) + '\n\nOutline:\n' + _outline_text(copy['markdown'])
        end = next((h['line'] - 1 for h in headings if h['line'] > found['line'] and h['level'] <= found['level']), len(lines))
        return _numbered(lines[found['line'] - 1:end], found['line'])
    if start_line is not None or end_line is not None:
        first, last = start_line or 1, min(end_line or len(lines), len(lines))
        if first > len(lines) or last < first:
            return OUT_OF_RANGE.format(n=len(lines))
        return _numbered(lines[first - 1:last], first)
    return json.dumps({'id': str(document), 'title': copy['title'], 'description': copy['description'],
                       'reads': 'your draft' if draft else f"the live version {live['version']}",
                       'lines': len(lines), 'characters': len(copy['markdown']), 'outline': outline(copy['markdown']),
                       'markdown': copy['markdown'][:MAX_MARKDOWN], 'truncated': len(copy['markdown']) > MAX_MARKDOWN})


def _save(config: dict[str, Any], document_id: str, change: Callable[[dict[str, Any]], dict[str, Any]]) -> str:
    """One save; a deadlock with a concurrent document delete, or the document vanishing mid-save, is retried once
    from the start in a new transaction (which finds the run or document gone and says so)."""
    try:
        return _save_once(config, document_id, change)
    except (errors.DeadlockDetected, errors.ForeignKeyViolation):
        return _save_once(config, document_id, change)


def _save_once(config: dict[str, Any], document_id: str, change: Callable[[dict[str, Any]], dict[str, Any]]) -> str:
    allowed(config, 'document', document_id, write=True)
    document = _document_id(document_id)
    if not config.get('session_id'):
        raise ValueError(NO_SESSION)
    with connect() as conn:
        session = conn.execute('SELECT id,project_id,status,turns FROM ka.agent_sessions WHERE id=%s FOR UPDATE',
                               (config['session_id'],)).fetchone()
        if session is None:
            raise ValueError(DOCUMENT_GONE)  # the run went with its document
        if session['status'] == 'closed':
            raise ValueError(CLOSED)
        project = session['project_id']
        live = conn.execute(LIVE, (document, project)).fetchone()
        if live is None:
            raise ValueError(DOCUMENT_GONE)
        current = conn.execute(WORKING + ' FOR UPDATE', (session['id'], document)).fetchone()
        base = dict(current or live)
        try:
            text = DraftText.model_validate(change(base))
        except ValidationError as exc:
            raise ValueError(str(exc.errors()[0]['msg']).removeprefix('Value error, ')) from None
        note = ''
        if current is not None:
            conn.execute('UPDATE ka.document_drafts SET title=%s,description=%s,markdown=%s,revision=revision+1 WHERE id=%s',
                         (text.title, text.description, text.markdown, current['id']))
            audit(conn, project, None, 'document.draft.updated', 'document_draft', current['id'],
                  {'session_id': session['id'], 'turn': session['turns']})
            draft_id = current['id']
        else:
            previous = conn.execute('''SELECT status,edited FROM ka.document_drafts WHERE session_id=%s AND document_id=%s
                ORDER BY created_at DESC,id DESC LIMIT 1''', (session['id'], document)).fetchone()
            if previous is not None:
                note = (EDITED if previous['status'] == 'open'
                        else (PROMOTED if previous['status'] == 'promoted' else DISCARDED).format(version=live['version']))
            content = {key: live[key] for key in ('purpose', 'audience', 'tags')} | text.model_dump()
            draft_id = create_draft(conn, project, None, content, origin='agent', document_id=document,
                                    session_id=session['id'])
        base_version = conn.execute('SELECT base_version_id FROM ka.document_drafts WHERE id=%s', (draft_id,)).fetchone()
        record_run_sources(conn, project, draft_id, document, base_version['base_version_id'], session['id'],
                           session['turns'])
    saved = SAVED.format(n=len(text.markdown.splitlines()))
    return f'{saved} {note}' if note else saved


async def get_document(context, document_id: str, section: str | None = None, start_line: int | None = None,
                       end_line: int | None = None) -> str:
    return await asyncio.to_thread(_get_document, context['config'], document_id, section, start_line, end_line)


async def save_document(context, document_id: str, markdown: str, title: str | None = None,
                        description: str | None = None) -> str:
    def change(base: dict[str, Any]) -> dict[str, Any]:
        return {'title': base['title'] if title is None else title,
                'description': base['description'] if description is None else description, 'markdown': markdown}
    return await asyncio.to_thread(_save, context['config'], document_id, change)


async def edit_document(context, document_id: str, old_text: str, new_text: str) -> str:
    return await asyncio.to_thread(_save, context['config'], document_id, _replace(old_text, new_text))
```

Note `LIVE` must select `v.version` (it already does). If `capabilities/inventory.py` does not export `project_of`, use the helper it uses for `_document` (the same name appears in `inventory._document`).

- [ ] **Step 6: Remove the inventory reader**

In `capabilities/inventory.py` delete `_document`, the async `get_document`, `MAX_MARKDOWN` and `NO_DOCUMENT` (keep `ATTACHED` and everything else).

- [ ] **Step 7: Update the catalog**

In `tools/catalog.py`:
- `Need = Literal['files', 'code', 'code_graph', 'documents', 'grounded', 'ssh', 'discovery', 'editable_document']`.
- Replace `class InventoryDocument` with:

```python
class GetDocument(Arguments):
    document_id: str = Field(min_length=1, max_length=64, description='A document id: one given to this run, or one from get_server_docs, get_service_docs, get_service_info or get_project_info.')
    section: str | None = Field(None, max_length=240, description='A heading of the document; returns that section with line numbers.')
    start_line: int | None = Field(None, ge=1, description='First line to read (1-based); alone, reads to the end.')
    end_line: int | None = Field(None, ge=1, description='Last line to read, inclusive; alone, reads from line 1.')
```

- Delete `class GetDoc`. Rename `class SaveDoc` → `SaveDocument` and `class EditDoc` → `EditDocument`, and add to each, as the first field: `document_id: str = Field(min_length=1, max_length=64, description='The id of a document given to this run to edit.')`.
- `GET_DOCUMENT = 'A document: without section or lines, its outline and text as JSON (text up to 50,000 characters); with section or start_line/end_line, those lines with numbers. Reads this run\'s draft of the document when it has one.\n\n' + INVENTORY`.
- Rename the usage constants `SAVE_DOC` → `SAVE_DOCUMENT`, `EDIT_DOC` → `EDIT_DOCUMENT` and delete `GET_DOC`; in their text, replace `get_doc` with `get_document` and describe the `document_id` argument ("Save your draft of the document with this id: …"). They say what the tool does, never how to work.
- The specs: replace the `get_document` spec with
  `ToolSpec('get_document', 'inventory', 'Read document', 'Read a document given to this run or attached to the inventory.', GET_DOCUMENT, GetDocument, _call(document_tools.get_document), None, _never)`;
  delete the three `get_doc`/`save_doc`/`edit_doc` specs and add
  `ToolSpec('save_document', 'documents', 'Save a document', "Save this run's draft of a document it may edit.", SAVE_DOCUMENT, SaveDocument, _call(document_tools.save_document), 'editable_document', _never)` and
  `ToolSpec('edit_document', 'documents', 'Edit a document', "Replace one passage in this run's draft of a document.", EDIT_DOCUMENT, EditDocument, _call(document_tools.edit_document), 'editable_document', _never)`.
- `DOCUMENT_EDIT_TOOLS = ('save_document', 'edit_document')` replaces `REVISION_TOOLS`; `RUN_ONLY_NEEDS = ('discovery', 'editable_document')  # never chosen for a saved agent: discovery runs only; documents are given per run`.
- `NEED_REASONS`: drop `'revision'`, add `'editable_document': 'Needs a document to edit'`.
- `needs_met`: replace the `revision` branch with `if need == 'editable_document': return any(entry.get('editable') for entry in config.get('document_access', []))`.

- [ ] **Step 8: Update the existing get_document tests**

In `tests/test_inventory_tools.py::test_get_document_reads_attached_documents_only_and_caps_them`: monkeypatch `document_tools.MAX_MARKDOWN` (import `from knowledge_admin.capabilities import document_tools`) instead of `inventory.MAX_MARKDOWN`, and expect `'This document is not available in this run.'` for the unknown and malformed ids. In `tests/test_document_draft_reads.py` the existing assertion (`json.loads(text)['markdown'] == '# Runbook v1'`) stays valid. Run `grep -rn "get_doc\b\|save_doc\|edit_doc\|REVISION_TOOLS\|revision_run" services/admin-api/src services/admin-api/tests` — the only remaining hits are in `agent_launch.py`, `generations_api.py`, `session_worker.py` and their tests (Tasks 3 and 6 remove them).

- [ ] **Step 9: Run the tests**

Run: `cd services/admin-api && uv run --frozen pytest -q tests/test_document_edit_tools.py tests/test_inventory_tools.py tests/test_document_draft_reads.py tests/test_tool_catalog.py tests/test_function_tools.py tests/test_agent_tools_api.py tests/test_discovery_tools.py`
Expected: PASS (one skipped test, for Task 3). `test_agent_launch.py` fails until Task 6 — do not run it here.

- [ ] **Step 10: Commit**

```bash
git add -A services/admin-api
git commit -m "feat(agents): get_document, save_document and edit_document take a document id, checked against the run's documents"
```

---

### Task 3: The worker gives tools the session, refreshes every draft, and offers the edit tools with an enum

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/knowledge_agents/native_sessions.py` (`load`)
- Modify: `services/admin-api/src/knowledge_admin/knowledge_agents/session_worker.py:205-232`
- Modify: `services/admin-api/src/knowledge_admin/document_drafts.py` (`refresh_run_sources_safely`)
- Modify: `services/admin-api/src/knowledge_admin/tools/sdk.py` (`function_tool`, `_function_tool`)
- Modify: `services/admin-api/src/knowledge_admin/agents.py` (`create_agent`, `get_prompt`)
- Test: `services/admin-api/tests/test_document_edit_tools.py` (the skipped test), `tests/test_document_tool_binding.py` (create)

**Interfaces:**
- Consumes: `DOCUMENT_EDIT_TOOLS` (Task 2).
- Produces: `function_tool(spec, usage, choices=None)` where `choices: list[dict] | None` are `document_access` entries; `ENUM_LIMIT = 50` in `tools/sdk.py`. `refresh_run_sources_safely(conn, session_id, turn)` refreshes every open, unedited draft of the session.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_document_tool_binding.py`:

```python
"""The edit tools list only the run's editable documents in their schema; the prompt lists the run's documents."""
from knowledge_admin.tools.catalog import CATALOG
from knowledge_admin.tools.sdk import ENUM_LIMIT, function_tool

ACCESS = [{'id': 'd1', 'title': 'Runbook', 'editable': True}, {'id': 'd2', 'title': 'Overview', 'editable': False}]


def test_the_edit_tools_schema_lists_only_the_editable_documents():
    tool = function_tool(CATALOG['save_document'], 'Save.', ACCESS)
    assert tool.params_json_schema['properties']['document_id']['enum'] == ['d1']
    assert 'Runbook (d1)' in tool.description and 'Overview' not in tool.description


def test_without_choices_or_above_the_limit_there_is_no_enum():
    assert 'enum' not in function_tool(CATALOG['save_document'], 'Save.').params_json_schema['properties']['document_id']
    many = [{'id': f'd{n}', 'title': f'T{n}', 'editable': True} for n in range(ENUM_LIMIT + 1)]
    assert 'enum' not in function_tool(CATALOG['save_document'], 'Save.', many).params_json_schema['properties']['document_id']
```

In `tests/test_document_edit_tools.py`, remove the skip from `test_a_revision_turn_never_turns_its_answer_into_a_draft_and_refreshes_sources`, rename it `test_a_turn_refreshes_the_sources_of_every_draft_the_run_wrote`, and make it save two documents before the turn ends: give the fixture config a second editable document (`make_document(rev.docs, 'overview', 'Overview')` from `drafts_world`), call `save_document` for both, record a `get_document` read of a third document in turn 1's events (as the existing test does for one), finish the turn through the worker path the existing test uses, and assert both drafts list the third document as a `read` source and the session's `draft` column stays null.

- [ ] **Step 2: Run them to verify they fail**

Run: `cd services/admin-api && uv run --frozen pytest -q tests/test_document_tool_binding.py tests/test_document_edit_tools.py`
Expected: FAIL (`ImportError: ENUM_LIMIT`; the refresh test finds sources on one draft only).

- [ ] **Step 3: Implement**

`tools/sdk.py`:

```python
ENUM_LIMIT = 50  # above this, the schema lists no choices; the tool's own check still applies


def function_tool(spec: ToolSpec, usage: str, choices: list[dict[str, Any]] | None = None) -> FunctionTool:
    """One catalog tool as an SDK function tool: the usage text is its description (see _function_tool). choices: the
    run's document_access entries; an edit tool's document_id then lists only the editable ones (up to ENUM_LIMIT)."""
    schema = spec.arguments.model_json_schema()
    editable = [entry for entry in choices or [] if entry.get('editable')]
    if editable and len(editable) <= ENUM_LIMIT and 'document_id' in schema.get('properties', {}):
        schema['properties']['document_id']['enum'] = [str(entry['id']) for entry in editable]
        usage = usage + '\n\nDocuments you may change: ' + ', '.join(f"{entry['title']} ({entry['id']})" for entry in editable)
    return _function_tool(spec.name, usage, spec.arguments, spec.run, schema)
```

and give `_function_tool` a last parameter `schema: dict[str, Any] | None = None`, using `ensure_strict_json_schema(schema if schema is not None else arguments.model_json_schema())`. `expert_tool` keeps calling it without a schema.

`agents.py` `create_agent`: `tools = [function_tool(spec, usage, config.get('document_access') if spec.name in DOCUMENT_EDIT_TOOLS else None) for spec, usage in offered]` (import `DOCUMENT_EDIT_TOOLS` from `knowledge_admin.tools.catalog`). In `get_prompt`, after the services block, add:

```python
    if config.get('document_access'):
        resources['documents_in_this_run'] = [{key: entry[key] for key in ('id', 'title', 'editable')}
                                              for entry in config['document_access']]
```

`native_sessions.py` `load`: after `config.pop('record', None)` add `config['session_id'] = str(self.id)  # for the document tools; never pinned`.

`session_worker.py` (the finish-turn block): replace

```python
            if updated is not None and updated['purpose'] == 'revision':
                refresh_run_sources_safely(conn, self.id, number)  # the tools saved the draft; reads may come later
```

with

```python
            if updated is not None:
                refresh_run_sources_safely(conn, self.id, number)  # drafts the tools saved; reads may come later
```

and in its docstring replace "An Ask agent session's draft is saved by its tools; the turn only refreshes its sources." with "Drafts the document tools saved get their sources refreshed."

`document_drafts.py` `refresh_run_sources_safely`: loop over every open, unedited draft of the session:

```python
            drafts = conn.execute('''SELECT id,project_id,document_id,base_version_id FROM ka.document_drafts
                WHERE session_id=%s AND status='open' AND NOT edited ORDER BY created_at,id FOR UPDATE''',
                (session_id,)).fetchall()
            for draft in drafts:
                record_run_sources(conn, draft['project_id'], draft['id'], draft['document_id'], draft['base_version_id'],
                                   session_id, turn)
```

and its docstring: "A turn ended: recompute the sources of each of the run's working drafts …".

- [ ] **Step 4: Run the tests**

Run: `cd services/admin-api && uv run --frozen pytest -q tests/test_document_tool_binding.py tests/test_document_edit_tools.py tests/test_function_tools.py tests/test_document_drafts_worker.py tests/test_native_sessions.py tests/test_session_worker.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A services/admin-api
git commit -m "feat(agents): the worker passes the session to the document tools and refreshes every run draft; edit tools list the editable documents"
```

---

### Task 4: Migration 0100 — the Ask agent prompt in the library, open Ask agent runs closed

**Files:**
- Create: `db/migrations/0100_sessions_api.up.sql`, `db/migrations/0100_sessions_api.down.sql`
- Create: `services/admin-api/tests/test_sessions_api_migration.py`
- Modify: `db/tests/run.sh` (one more `down 1` step at the front of the down chain, and one more check in the up checks)
- Modify: `docs/migrations.md` (new `## 0100_sessions_api` section after `## 0099_document_agent`), `docs/database.md` (the prompts paragraph: mention the seeded "Revise a document" prompt)

**Interfaces:**
- Produces: per workspace, a prompt with id `md5('knowledge-agent-v3:prompts:document-revision:' || project_id)::uuid`, name `Revise a document`, tags `{document-revision}`. Task 6 makes it the options' default.

- [ ] **Step 1: Write the failing test**

```python
"""0100: the Ask agent prompt is in the prompt library; open Ask agent runs from before are closed."""
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from test_document_agent_migration import session, world

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0100_sessions_api.up.sql', '0100_sessions_api.down.sql'
PROMPT = """SELECT p.tags,p.enabled,v.content FROM ka.documentation_prompts p JOIN ka.documentation_prompt_versions v
    ON v.id=p.active_version_id WHERE p.project_id=%s AND p.name='Revise a document'"""


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def test_every_workspace_gets_the_revise_prompt_with_the_editing_rules(store):
    with store.connect() as conn:
        project = store.project(conn, f'shop-{uuid4().hex[:6]}')
        prompt = conn.execute(PROMPT, (project,)).fetchone()
    assert prompt['tags'] == ['document-revision'] and prompt['enabled']
    for words in ('get_document', 'edit_document', 'save_document', 'ask', 'never'):
        assert words in prompt['content']


def test_up_closes_open_ask_agent_runs_and_their_queued_messages_and_nothing_else(store):
    with store.connect() as conn:
        project, runbook = world(conn, store)
        open_run, closed_run = session(conn, project, runbook), session(conn, project, runbook)
        conn.execute("UPDATE ka.agent_sessions SET status='closed',closed_at=now() WHERE id=%s", (closed_run,))
        conn.execute("UPDATE ka.agent_sessions SET status='working' WHERE id=%s", (open_run,))
        conn.execute("""INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text,status)
            SELECT %s,1,actor_id,'Next.','queued' FROM ka.agent_sessions WHERE id=%s""", (open_run, open_run))
        conn.commit()  # the migration files carry their own BEGIN/COMMIT
        run(conn, DOWN)
        run(conn, UP)
        rows = {row['id']: row for row in conn.execute('SELECT id,status,status_message FROM ka.agent_sessions WHERE id=ANY(%s)',
                                                       ([open_run, closed_run],))}
        queued = conn.execute("SELECT status FROM ka.agent_session_inputs WHERE session_id=%s", (open_run,)).fetchone()
    assert rows[open_run]['status'] == 'closed' and rows[open_run]['status_message'] == 'Closed by an update; start a new chat.'
    assert rows[closed_run]['status_message'] != 'Closed by an update; start a new chat.'
    assert queued['status'] == 'discarded'


def test_down_refuses_a_customized_prompt_then_round_trips(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        conn.execute("UPDATE ka.documentation_prompts SET revision=revision+1 WHERE project_id=%s AND name='Revise a document'", (project,))
        conn.commit()
        with pytest.raises(psycopg.errors.RaiseException, match='customized'):
            run(conn, DOWN)
        conn.rollback()
        conn.execute("UPDATE ka.documentation_prompts SET revision=revision-1 WHERE project_id=%s AND name='Revise a document'", (project,))
        conn.commit()
        run(conn, DOWN)
        assert conn.execute(PROMPT, (project,)).fetchone() is None
        run(conn, UP)
        assert conn.execute(PROMPT, (project,)).fetchone() is not None
```

If `session()` in `test_document_agent_migration.py` does not accept a status, the test sets it with the UPDATEs shown. If the customized check in the down migration uses something other than `revision` (see Step 3), make the test's UPDATE match it.

- [ ] **Step 2: Run it to verify it fails**

Run: `cd services/admin-api && uv run --frozen pytest -q tests/test_sessions_api_migration.py`
Expected: FAIL (the migration file does not exist; the test template applies all `*.up.sql`, so the prompt is missing).

- [ ] **Step 3: Write the migration**

`db/migrations/0100_sessions_api.up.sql`:

```sql
BEGIN;
SET search_path = ka, public;

-- One sessions API: how an Ask agent run works is now its prompt's text, in the prompt library ("Revise a document",
-- tag document-revision), not text the code appends. The document tools became get_document, edit_document and
-- save_document, taking a document id; open Ask agent runs were pinned with the old tools, so they are closed.
CREATE FUNCTION seed_document_revision_prompt(workspace_id uuid) RETURNS void
LANGUAGE plpgsql SET search_path=ka,pg_temp AS $seed$
DECLARE p uuid; v uuid; inserted uuid;
BEGIN
    p := md5('knowledge-agent-v3:prompts:document-revision:' || workspace_id::text)::uuid;
    v := md5('knowledge-agent-v3:prompts:document-revision:v1:' || workspace_id::text)::uuid;
    INSERT INTO documentation_prompts(id,project_id,name,description,input_kind,tags,next_version)
    VALUES(p,workspace_id,'Revise a document','Change one technical document at a person''s request, as a draft for review.',
           'mixed',ARRAY['document-revision'],2)
    ON CONFLICT DO NOTHING RETURNING id INTO inserted;
    IF inserted IS NOT NULL THEN
        INSERT INTO documentation_prompt_versions(id,prompt_id,version,content,activated_at)
        VALUES(v,p,1,$prompt$You revise one technical document at a person's request. Read the parts of the document you change before you change them, and check facts against the servers, code and documents you were given. Keep the document's structure and voice; change only what the request asks for. Never include passwords, keys or tokens.

Rules:
- Read the document with get_document: without section or lines for its outline and text, with section for one section, with start_line and end_line for lines.
- Change the document only with edit_document (one exact passage) or save_document (the complete text). Each save updates your draft; a person reviews it and decides whether it becomes the live version. Never say the change is live.
- When the request is unclear, ask in plain chat before you change the document.
- When you are done, say in a few sentences what you changed.$prompt$,now());
        UPDATE documentation_prompts SET active_version_id=v WHERE id=p;
    END IF;
END $seed$;
CREATE FUNCTION seed_document_revision_on_project() RETURNS trigger
LANGUAGE plpgsql SET search_path=ka,pg_temp AS $$
BEGIN
    PERFORM seed_document_revision_prompt(NEW.id);
    RETURN NEW;
END $$;
CREATE TRIGGER seed_document_revision AFTER INSERT ON projects
    FOR EACH ROW EXECUTE FUNCTION seed_document_revision_on_project();
SELECT seed_document_revision_prompt(id) FROM projects;

UPDATE agent_session_inputs SET status='discarded'
    WHERE status='queued' AND session_id IN (SELECT id FROM agent_sessions WHERE purpose='revision' AND status<>'closed');
UPDATE agent_sessions SET status='closed', closed_at=now(), status_message='Closed by an update; start a new chat.'
    WHERE purpose='revision' AND status<>'closed';
COMMENT ON COLUMN agent_sessions.target_document_id IS
    'The document an Ask agent run was started from (for lists); set exactly when purpose is revision. The agent and its tools never read it.';

COMMIT;
```

`db/migrations/0100_sessions_api.down.sql` — copy the structure of `0097_server_discovery.down.sql` lines 14–27 (the customized-prompt guard, the delete, the trigger and function drops) with the names `document-revision`, `seed_document_revision`, `seed_document_revision_on_project`, `seed_document_revision_prompt(uuid)` and the message `'The Revise a document prompt has been customized; preserve it before rollback'`; then restore the 0099 comment:

```sql
COMMENT ON COLUMN agent_sessions.target_document_id IS 'The document an Ask agent run revises; set exactly when purpose is revision.';
```

Wrap it in `BEGIN; SET search_path = ka, public; … COMMIT;` like the others. Closed runs stay closed.

- [ ] **Step 4: Extend `db/tests/run.sh`**

Before the existing first `run_migrate postgres down 1` (the one followed by the 0099 `agent_removed` check), add:

```sh
run_migrate postgres down 1 >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
revise_removed=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT to_regprocedure('ka.seed_document_revision_prompt(uuid)') IS NULL")
[ "$revise_removed" = t ] || { echo 'FAIL: 0100 down left the Revise a document seed' >&2; exit 1; }
```

and after the `PASS: Ask agent runs name their document.` line add:

```sh
revise=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT to_regprocedure('ka.seed_document_revision_prompt(uuid)') IS NOT NULL")
[ "$revise" = t ] || { echo 'FAIL: 0100 Revise a document seed missing' >&2; exit 1; }
printf '%s\n' 'PASS: the Revise a document prompt is seeded.'
```

(Do not run `run.sh`: it is a `make db-test` target. The pytest migration tests cover the SQL.)

- [ ] **Step 5: Document it**

`docs/migrations.md`, after the 0099 section:

```markdown
## 0100_sessions_api

Seeds the prompt **Revise a document** (tag `document-revision`) in every workspace and for new ones: the Ask agent's
editing rules, now ordinary prompt text. Open Ask agent runs (pinned with the old document tools) are closed with "Closed
by an update; start a new chat."; their queued messages are discarded. `target_document_id` now only records where a run
was started. The down migration refuses while the seeded prompt is customized and removes it; closed runs stay closed.
```

- [ ] **Step 6: Run the tests**

Run: `cd services/admin-api && uv run --frozen pytest -q tests/test_sessions_api_migration.py tests/test_document_agent_migration.py tests/test_discovery_migration.py tests/test_prompt_tags_migration.py`.
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add db services/admin-api/tests/test_sessions_api_migration.py docs/migrations.md docs/database.md
git commit -m "feat(db): 0100 seeds the Revise a document prompt and closes open Ask agent runs"
```

---

### Task 5: The `sessions` package — run operations and lists at `/api/sessions`

**Files:**
- Create: `services/admin-api/src/knowledge_admin/sessions/__init__.py` (empty docstring module), `sessions/rows.py`, `sessions/api.py`
- Modify: `services/admin-api/src/knowledge_admin/generations_api.py` (keeps only Generate documents' create and context), `api.py`, `agent_sessions.py`, `discovery.py` (imports)
- Modify tests: every file under `services/admin-api/tests` that calls `/api/generations/sessions/…` or imports moved names
- Test: `services/admin-api/tests/test_sessions_api.py` (create)

**Interfaces:**
- Produces, `knowledge_admin.sessions.rows`: `session_detail(conn, project, session_id)`, `SUMMARY`, `DETAIL`, `stream_event(row)`, `MessageInput`, `Selection`, `NO_MODEL`, `CLOSED`, `NOTHING_TO_STOP` (moved verbatim from `generations_api.py`).
- Produces, `knowledge_admin.sessions.api`: `build_api(app_name, store) -> APIRouter` with prefix `/sessions`; `KEEPALIVE_SECONDS`, `DEFAULT_KEEPALIVE_SECONDS`, `HISTORY_LIMIT`, `LIVE_UNAVAILABLE` (moved). Routes: `GET ''` (`purpose`, `document_id`, `offset` query), `GET /{id}`, `POST /{id}/messages`, `/{id}/stop`, `/{id}/close`, `GET /{id}/events/history`, `GET /{id}/events`. Task 6 adds options, preview and start to this router.
- `GET /api/sessions` rows: `SUMMARY` columns plus `started_by` (principal email) and `draft_id` (the latest open draft), newest activity first with closed runs last when `document_id` is given (today's Ask agent order), newest first otherwise; 50 per page with `offset`.

- [ ] **Step 1: Write the failing test**

```python
"""/api/sessions: the run operations for every kind of run, and the lists."""
from test_agent_launch_world import ask, post  # noqa: F401  (fixtures, created in this task — see Step 3)
from test_generations import sessions  # noqa: F401  (fixture)
from test_repositories import HEADERS


def test_old_session_routes_are_gone(sessions):
    some = '00000000-0000-0000-0000-000000000001'
    for method, path in (('get', f'/api/generations/sessions/{some}'), ('post', f'/api/generations/sessions/{some}/close'),
                         ('post', f'/api/generations/sessions/{some}/document'), ('get', '/api/agent-sessions/options')):
        assert getattr(sessions.client, method)(path, headers=HEADERS).status_code in (404, 405)


def test_the_lists_filter_by_document_and_by_purpose(ask):
    first, second = post(ask).json(), post(ask, message='Remove the old backup steps.').json()
    client = ask.s.client
    assert client.post(f"/api/sessions/{first['id']}/close", headers=HEADERS).status_code == 200
    listed = client.get(f"/api/sessions?document_id={ask.document['id']}", headers=HEADERS).json()['sessions']
    assert [row['id'] for row in listed] == [second['id'], first['id']]
    assert listed[0]['draft_id'] is None and listed[0]['started_by']
    assert client.get('/api/sessions?purpose=documents', headers=HEADERS).json()['sessions'] == []
    assert client.post(f"/api/sessions/{second['id']}/messages", headers=HEADERS, json={'text': 'Also cron.'}).status_code == 202
```

(`post(ask, …)` uses the new start body from Task 6, so this test passes only after Task 6; until then run only `test_old_session_routes_are_gone`.)

- [ ] **Step 2: Run it to verify it fails**

Run: `cd services/admin-api && uv run --frozen pytest -q tests/test_sessions_api.py::test_old_session_routes_are_gone`
Expected: FAIL (the old routes still answer).

- [ ] **Step 3: Move the code**

1. Create `sessions/rows.py` with the module docstring `"""What a session looks like to the API: its row, its detail and its events."""` and move into it, verbatim, from `generations_api.py`: `NO_MODEL`, `CLOSED`, `NOTHING_TO_STOP`, `SELECTION`, `Selection`, `MessageInput`, `_select`, `SUMMARY`, `DETAIL`, `session_detail`, `stream_event`, with the imports they use. In `session_detail` keep the `config`/`reviews` branch for `('test', 'event', 'revision')`.
2. Create `sessions/api.py` with `build_api(app_name, store)`: `APIRouter(prefix='/sessions')`, the `database()`, `audit()`, `locked()` and `discard()` helpers and the routes `/{session_id}` (GET), `/{session_id}/messages`, `/stop`, `/close`, `/events/history`, `/events`, moved verbatim from `generations_api.build_api` with `'/sessions/{session_id}…'` paths shortened to `'/{session_id}…'`, plus `DEFAULT_KEEPALIVE_SECONDS`, `KEEPALIVE_SECONDS`, `HISTORY_LIMIT`, `LIVE_UNAVAILABLE`. The 503 text in `database()` becomes `'Agent session storage is not configured.'`. Audit actions keep their names (`generation.session.stopped`, `generation.session.closed`) — they are a record, not an API.
3. Add the list route to `sessions/api.py`:

```python
LIST = SUMMARY.replace('FROM ka.agent_sessions s', ''',p.email AS started_by,
        (SELECT x.id FROM ka.document_drafts x WHERE x.session_id=s.id AND x.status='open'
         ORDER BY x.created_at DESC,x.id DESC LIMIT 1) AS draft_id
        FROM ka.agent_sessions s LEFT JOIN ka.principals p ON p.id=s.actor_id''', 1)

    @router.get('')
    def sessions(response: Response, purpose: SessionPurpose | None = None, document_id: UUID | None = None,
                 offset: int = Query(0, ge=0)) -> dict:
        """Runs, newest first: those started from a document (open first, then by activity), or of one purpose."""
        response.headers['Cache-Control'] = 'no-store'
        db = database()
        with db.connect() as conn:
            close_idle(conn)
            where, values = ['s.project_id=%s'], [db.project(conn, app_name)]
            if document_id is not None:
                where.append('s.target_document_id=%s'); values.append(document_id)
            if purpose is not None:
                where.append('s.purpose=%s'); values.append(purpose)
            order = "s.status='closed',s.last_activity_at DESC,s.id" if document_id else 's.created_at DESC,s.id'
            return {'sessions': conn.execute(f"{LIST} WHERE {' AND '.join(where)} ORDER BY {order} LIMIT 50 OFFSET %s",
                                             (*values, offset)).fetchall()}
```

with `SessionPurpose = Literal['documents', 'test', 'event', 'discovery', 'revision']` in `rows.py`. Check that `_select()`'s text contains `FROM ka.agent_sessions s` exactly once (it does); assert it at import with `assert LIST != SUMMARY`.
4. `generations_api.py` keeps only `SessionInput`, `run_folders`, `NOTHING_CHOSEN`, the `/context` route and `POST /sessions` (create), importing what it needs from `sessions.rows`. Delete its `GET /sessions` (the list moved), all per-id routes, and the `/sessions/{id}/document` route with `ONLY_DOCUMENTS` (Save as draft is gone). Its module docstring says it is Generate documents' form API until part 3.
5. `api.py`: `from knowledge_admin.sessions import api as sessions_api` and `router.include_router(sessions_api.build_api(app_name, repository_store))` next to `generations_api`.
6. Update imports: `agent_sessions.py` (`session_detail` from `knowledge_admin.sessions.rows`, and its docstring's "is the session API in generations_api" → "is the session API in sessions/api.py"), `discovery.py` (`NO_MODEL, Selection`), `agent_launch.py` (`NO_MODEL, Selection, session_detail`; Task 6 deletes the file), `native_sessions.py` docstring reference.
7. Tests: in every test file, replace `/api/generations/sessions/` (with the trailing slash: per-id routes) by `/api/sessions/`, and `/api/generations/sessions?` (the list) by `/api/sessions?purpose=documents&`. `POST /api/generations/sessions` (create, no trailing slash) stays. Replace `generations_api.KEEPALIVE_SECONDS`/`DEFAULT_KEEPALIVE_SECONDS`/`LIVE_UNAVAILABLE` and `import generations_api` with `from knowledge_admin.sessions import api as sessions_api` and `sessions_api.…` in `test_generations.py` and `test_session_stream.py`. Delete the tests of Save as draft (`grep -n "/document'" tests/test_generations.py tests/test_document_drafts_worker.py` and `ONLY_DOCUMENTS` in `tests/test_test_sessions.py`); keep every other assertion. Create `tests/test_agent_launch_world.py` by moving the `ask` fixture, `post()` and the `keyless` fixture out of `tests/test_agent_launch.py` unchanged (Task 6 changes them).

- [ ] **Step 4: Run the tests**

Run: `cd services/admin-api && uv run --frozen pytest -q tests/test_sessions_api.py::test_old_session_routes_are_gone tests/test_generations.py tests/test_session_stream.py tests/test_native_sessions.py tests/test_tool_counts.py tests/test_subagents.py tests/test_test_sessions.py tests/test_test_session_worker.py tests/test_event_sessions.py tests/test_expert_calls.py tests/test_document_drafts_worker.py tests/test_discovery_runs.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A services/admin-api
git commit -m "refactor(agents): the run operations and lists move to /api/sessions; Save as draft is gone"
```

---

### Task 6: One start — options, preview and start at `/api/sessions`; `agent_launch` deleted

**Files:**
- Create: `services/admin-api/src/knowledge_admin/sessions/start.py`
- Modify: `services/admin-api/src/knowledge_admin/sessions/api.py` (mount options, preview, start)
- Delete: `services/admin-api/src/knowledge_admin/agent_launch.py`; `api.py` stops including it
- Modify: `services/admin-api/src/knowledge_admin/session_text.py` or the module that builds `agent_system_prompt` only if Step 5 needs it (it should not)
- Test: rename `services/admin-api/tests/test_agent_launch.py` → `tests/test_sessions_start.py`; update `tests/test_agent_launch_world.py`

**Interfaces:**
- Consumes: `allowed` (Task 1), `DOCUMENT_EDIT_TOOLS`, `RUN_ONLY_NEEDS` (Task 2), the seeded prompt id (Task 4), `sessions.rows.session_detail`, `Selection`, `NO_MODEL` (Task 5).
- Produces: `StartInput` (below), `build(conn, project, value, session_id) -> (pinned, model, document)`, `options(conn, project, actor, document_id) -> dict`, `REVISION_TAG = 'document-revision'`, `revise_prompt_id(project) -> UUID`.
- Start body (JSON): `{message, model_id, prompt_id?, system_prompt, context?, resources: {server_ids, service_ids, environment_ids, code_project_ids, collection_ids, documents: [{id, editable}]}, tools, expert_ids, subagents, started_from: {document_id}, agent_id?}`.
- Options (`GET /api/sessions/options?document_id=`): `{models, default_model_id, prompts, default_prompt_id, resources: {servers, services, environments, code_projects, collections, documents: [{id, name, linked: true, editable: true}]}, tools, experts}` — `default_prompt` and `fixed_tools` are gone; `tools` now include `save_document`, `edit_document` (`needs: 'editable_document'`, `requires: 'a document to edit'`) and `get_document`.

- [ ] **Step 1: Move and update the tests**

`git mv services/admin-api/tests/test_agent_launch.py services/admin-api/tests/test_sessions_start.py`. In `tests/test_agent_launch_world.py` the body becomes:

```python
    body = {'started_from': {'document_id': document['id']}, 'message': 'Add a section on log rotation.\nKeep it short.',
            'model_id': s.gpt, 'system_prompt': 'You revise documents.', 'context': f"Document: Payments runbook (id {document['id']})",
            'resources': {'service_ids': [s.service], 'server_ids': [web['id']],
                          'documents': [{'id': document['id'], 'editable': True}]},
            'tools': ['ssh_run', 'get_service_info', 'get_document', 'save_document', 'edit_document'],
            'expert_ids': [], 'subagents': False}
```

and `post()` posts to `f'/api/sessions{path}'`. In `test_sessions_start.py` update each test:
- options URL → `/api/sessions/options?document_id=…`; replace the `default_prompt`/`fixed_tools` assertions with `assert options['default_prompt_id'] == next(p['id'] for p in options['prompts'] if p['name'] == 'Revise a document')`, `assert options['resources']['documents'] == [{'id': ask.document['id'], 'name': 'Payments runbook', 'linked': True, 'editable': True}]`, and `tools['save_document']['requires'] == 'a document to edit'`.
- the preview test: `revision_run` assertions become `pinned['document_access'] == [{'id': ask.document['id'], 'title': 'Payments runbook', 'editable': True}]` and `preview.json()['config'] == pinned`; `first_message` equals `ask.body['message']`; the edit tools are `on` with `subagent_allowed` false; `'# Context\n\nDocument: Payments runbook' in pinned['system_prompt']['content']`; and replace the outline assertions with `assert '## Backup' not in pinned['system_prompt']['content']` (no document text or outline is appended).
- refusals: `target={'document_id': …}` → `started_from={'document_id': …}` (still 404 `Document not found`), and the deleted-before-insert test patches `knowledge_admin.sessions.start.build`.
- the list test moves to `test_sessions_api.py` (Task 5's second test); delete it here. The audit assertion keeps `details['document_id']` and drops `details['kind']`.
- session routes → `/api/sessions/{id}`.

Add these tests to `test_sessions_start.py`:

```python
def test_a_saved_agent_or_no_starting_point_is_refused(ask):
    for change, status, message in (({'agent_id': 'code'}, 400, 'Saved agents start from their Test tab for now.'),
                                    ({'started_from': None}, 422, None)):
        for path in ('/preview', ''):
            response = post(ask, path, **change)
            assert response.status_code == status, response.text
            if message:
                assert response.json()['error']['message'] == message


def test_the_editable_document_must_exist_and_twice_listed_documents_are_one_editable_entry(ask):
    gone = post(ask, resources={**ask.body['resources'], 'documents': [{'id': str(uuid4()), 'editable': True}]})
    assert gone.status_code == 404 and gone.json()['error']['message'] == 'Document not found'
    twice = [{'id': ask.document['id'], 'editable': False}, {'id': ask.document['id'], 'editable': True}]
    pinned = post(ask, '/preview', resources={**ask.body['resources'], 'documents': twice}).json()['config']
    assert pinned['document_access'] == [{'id': ask.document['id'], 'title': 'Payments runbook', 'editable': True}]


def test_nothing_but_the_context_is_appended_to_the_prompt(ask):
    pinned = post(ask, '/preview', context='').json()['config']
    assert pinned['system_prompt']['content'] == 'You revise documents.'
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd services/admin-api && uv run --frozen pytest -q tests/test_sessions_start.py`
Expected: FAIL (404 on `/api/sessions/options`).

- [ ] **Step 3: Write `sessions/start.py`**

Start from `agent_launch.py` (the module being replaced) and change it as follows; everything not mentioned (`TITLE_LENGTH`, `NEEDS`, `REQUIRES`, `NO_EXPERT`, `NOT_SET_UP`, `NOT_ALLOWED`, `UNKNOWN_TOOL`, `DOCUMENT_NOT_FOUND`, `DOCUMENT_FOREIGN_KEY`, `server_reason`, the keyless-server narrowing in `build`, the unmet-needs check, the experts check, the model check, the options' `brings()`/`listed()` logic and `default_model_id`) moves over unchanged.

- Module docstring: `"""Starting a session: the options a start panel needs, Preview (exactly what Start pins) and Start. One request shape for every ad-hoc run; where it is started from is a label (started_from), and what it may use is pinned in its config. Saved agents (agent_id) join in part 2."""`
- Delete `RevisionTarget`, `DEFAULT_REVISION_PROMPT`, `RULES`, `RESOURCE_NAMES`, `RUNS`, `revision_context`, and the `revision_run` key.
- `REQUIRES` gains `'editable_document': 'a document to edit'`.
- Input models:

```python
NO_SAVED_AGENTS = 'Saved agents start from their Test tab for now.'


class DocumentAccess(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: UUID
    editable: bool = False


class Resources(Selection):
    documents: list[DocumentAccess] = Field(default_factory=list, max_length=20)


class StartedFrom(BaseModel):
    model_config = ConfigDict(extra='forbid')
    document_id: UUID


class StartInput(BaseModel):
    """What a start panel sends: the first message, the model and prompt, facts from where it starts (context), what
    the run may use (resources, tools, experts, subagents) and where it was started from (a label for lists)."""
    model_config = ConfigDict(extra='forbid')
    agent_id: str | None = None
    started_from: StartedFrom
    message: str = Field(max_length=20000)
    model_id: UUID
    prompt_id: UUID | None = None
    system_prompt: str = Field(max_length=20000)
    context: str = Field(default='', max_length=20000)
    resources: Resources = Field(default_factory=Resources)
    tools: list[str] = Field(default_factory=list, max_length=100)
    expert_ids: list[UUID] = Field(default_factory=list, max_length=50)
    subagents: bool = False

    @model_validator(mode='after')
    def valid(self):
        for name in ('message', 'system_prompt', 'context'):
            value = getattr(self, name)
            if '\x00' in value:
                raise ValueError('Text cannot contain null characters')
            setattr(self, name, value.strip())
        if not self.message:
            raise ValueError('Enter a message')
        if not self.system_prompt:
            raise ValueError('Enter a system prompt')
        self.tools = list(dict.fromkeys(self.tools))
        self.expert_ids = list(dict.fromkeys(self.expert_ids))
        merged: dict[UUID, bool] = {}
        for entry in self.resources.documents:  # one entry per document; editable wins
            merged[entry.id] = merged.get(entry.id, False) or entry.editable
        self.resources.documents = [DocumentAccess(id=key, editable=value) for key, value in merged.items()]
        return self
```

- `choosable()`: `spec.needs not in ('discovery', 'grounded') and spec.name != SUBAGENT` (the edit tools are now choosable here).
- `revise_prompt_id(project: UUID) -> UUID`: `UUID(hashlib.md5(f'knowledge-agent-v3:prompts:document-revision:{project}'.encode()).hexdigest())` (matches the migration's `md5(…)::uuid`).
- `document_access(conn, project, entries) -> list[dict]`: for each entry, `SELECT d.id,v.title FROM ka.documents d JOIN ka.document_versions v ON v.id=d.current_version_id WHERE d.id=%s AND d.project_id=%s`; a missing one raises `HTTPException(404, DOCUMENT_NOT_FOUND)`; returns `[{'id': str(row['id']), 'title': row['title'], 'editable': entry.editable}]`.
- `build(conn, project, value, session_id)`:
  - `if value.agent_id is not None: raise HTTPException(400, NO_SAVED_AGENTS)`
  - the started-from document must exist: `target = conn.execute('SELECT id FROM ka.documents WHERE id=%s AND project_id=%s', (value.started_from.document_id, project)).fetchone()`; none → 404 `DOCUMENT_NOT_FOUND`.
  - model check, server reasons, unknown tools (`names = {spec.name: spec for spec in choosable()}`; unknown = not in `names`) — as before.
  - `system_prompt = value.system_prompt + (f'\n\n# Context\n\n{value.context}' if value.context else '')`.
  - `pinned = ad_hoc_config(conn, project, model=model, system_prompt=system_prompt, selection=value.resources)` — `ad_hoc_config` reads only the five id lists from `selection`, so passing `Resources` works.
  - `pinned['document_access'] = document_access(conn, project, value.resources.documents)` **before** the unmet-needs check (so the edit tools' need is judged with it).
  - tool modes: `item['mode'] = 'on' if item['tool'] in chosen else 'off'` for catalog tools; `run_subagent` from `value.subagents`; for `DOCUMENT_EDIT_TOOLS` also `subagent_allowed=False`.
  - `pinned['project_id'] = str(project)`; return `(jsonable_encoder(pinned), model, target)`.
- `options(conn, project, actor, document_id: UUID | None)`: as the old `options` route body, with: no document → no `linked` marks and `documents: []`; with one → `target_document(conn, project, document_id)` gives the linked ids and `documents: [{'id': document['id'], 'name': document['title'], 'linked': True, 'editable': True}]`; `last` model query keyed by `target_document_id` only when a document is given (else the actor's last ad-hoc run: `WHERE agent_id IS NULL AND actor_id=%s`); prompts tagged `REVISION_TAG`; `default_prompt_id = revise_prompt_id(project)` if that id is among the prompts, else the first prompt's id, else `None`; `tools` from `choosable()` (with `requires` from `REQUIRES`); no `fixed_tools`, no `default_prompt`.
- Routes, added to `sessions/api.py` `build_api` **before** the `/{session_id}` routes (so `options` and `preview` are not read as ids): `GET /options` (`document_id: UUID | None = Query(None)`), `POST /preview` (returns `{'config': pinned, 'first_message': value.message}`), `POST ''` (status 201): the old start body with `value.message`, `title = value.message.splitlines()[0][:TITLE_LENGTH]`, the INSERT's `target_document_id` = `value.started_from.document_id` and purpose `'revision'`, `prompt_snapshot = {'system_prompt': value.system_prompt, 'context': value.context, 'prompt_id': value.prompt_id}`, `context_snapshot = {'document_id': str(value.started_from.document_id)}`, the input row with `value.message`, and the audit action `agent_run.created` with details `{'document_id', 'model_id', 'prompt_id', 'tools_off', 'expert_ids', 'subagents'}`. Keep the `ForeignKeyViolation` → 404 handling.

Delete `agent_launch.py` and its `include_router` line in `api.py`.

- [ ] **Step 4: Run the tests**

Run: `cd services/admin-api && uv run --frozen pytest -q tests/test_sessions_start.py tests/test_sessions_api.py tests/test_document_edit_tools.py`
Expected: PASS.

- [ ] **Step 5: Search for leftovers**

Run: `grep -rn "agent_launch\|agent-sessions\|revision_run\|DEFAULT_REVISION_PROMPT\|get_doc\b\|save_doc\b\|edit_doc\b\|fixed_tools\|default_prompt\b" services/admin-api/src services/admin-api/tests`
Expected: no output.

- [ ] **Step 6: Run the whole backend suite**

Run: `cd services/admin-api && uv run --frozen pytest -q -p no:warnings`
Expected: PASS apart from the failures named in Global Constraints.

- [ ] **Step 7: Commit**

```bash
git add -A services/admin-api
git commit -m "feat(agents): one start for ad-hoc runs at /api/sessions (options, preview, start); documents are resources"
```

---

### Task 7: `lib/sessions.ts` — one client module for runs

**Files:**
- Create: `services/admin-ui/app/lib/sessions.ts`
- Delete: `services/admin-ui/app/lib/agentSessions.ts`
- Modify: `services/admin-ui/app/lib/generations.ts` (keeps only the Generate documents form), and every importer (list in Step 2)
- Test: `services/admin-ui/app/lib/__tests__/sessions.test.ts` (create)

**Interfaces:**
- Produces, `~/lib/sessions`: the types `SessionPurpose`, `SessionStatus`, `SessionFolder`, `SessionSummary`, `SessionDetail`, `SessionState`, `SessionEvent`, `statusLabels`, `formatTokens`, `HISTORY_PAGE`; calls `getSession`, `sessionEventsUrl`, `getSessionHistory`, `sendMessage`, `stopSession`, `closeSession`, `getSessions(filter: { purpose?: SessionPurpose; documentId?: string; offset?: number }, signal?)`, `getSessionOptions(documentId: string | null, signal?)`, `previewSession(value: StartInput)`, `startSession(value: StartInput)`; `sessionPath(id: string) => '/sessions/' + encodeURIComponent(id)`; `agentRunPath`, `toolApplies`, `savedIn` (from `agentSessions.ts`); types `AgentResource`, `AgentServer`, `AgentBringer`, `AgentTool`, `AgentOptions`, `AgentResources`, `StartInput`, `RunSummary` (renamed from `AgentRunSummary`).
- `SessionDetail` drops nothing; `StartInput` becomes `{ started_from: { document_id: string }; message: string; model_id: string; prompt_id: string | null; system_prompt: string; context: string; resources: AgentResources & { documents: { id: string; editable: boolean }[] }; tools: string[]; expert_ids: string[]; subagents: boolean }`.
- `AgentOptions` becomes `{ models; default_model_id; prompts; default_prompt_id: string | null; resources: { …the five kinds…; documents: { id: string; name: string; linked: boolean; editable: boolean }[] }; tools; experts }` (no `default_prompt`, no `fixed_tools`).
- `savedIn` recognises `['save_document', 'edit_document', 'save_doc', 'edit_doc']` (old runs' history).
- `toolApplies` gains `case 'editable_document': return true;` (the Ask agent always gives one; part 2 revisits).

- [ ] **Step 1: Write the failing test**

```ts
import { afterEach, expect, test, vi } from 'vitest';
import { getSessionOptions, getSessions, savedIn, sessionPath, startSession } from '../sessions';
import type { Turn } from '../turns';

afterEach(() => vi.unstubAllGlobals());
function api() {
  const calls: string[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? 'GET'} ${url}`);
    return new Response(JSON.stringify({ sessions: [] }), { status: 200 });
  }));
  return calls;
}

test('the calls use /api/sessions', async () => {
  const calls = api();
  await getSessions({ documentId: 'd 1' });
  await getSessions({ purpose: 'documents', offset: 50 });
  await getSessionOptions('d1');
  await getSessionOptions(null);
  await startSession({ started_from: { document_id: 'd1' }, message: 'Hi', model_id: 'm', prompt_id: null, system_prompt: 'S',
    context: '', resources: { server_ids: [], service_ids: [], environment_ids: [], code_project_ids: [], collection_ids: [], documents: [] },
    tools: [], expert_ids: [], subagents: false });
  expect(calls).toEqual(['GET /api/sessions?document_id=d%201', 'GET /api/sessions?purpose=documents&offset=50',
    'GET /api/sessions/options?document_id=d1', 'GET /api/sessions/options', 'POST /api/sessions']);
  expect(sessionPath('a/b')).toBe('/sessions/a%2Fb');
});

test('a turn saved its draft with the new or the old save tools', () => {
  const turn = (name: string) => ({ work: [{ kind: 'tool', name, isError: false, preview: 'Saved draft (3 lines).' }] }) as unknown as Turn;
  expect(['save_document', 'edit_document', 'save_doc', 'edit_doc'].map(name => savedIn(turn(name)))).toEqual([true, true, true, true]);
  expect(savedIn(turn('get_document'))).toBe(false);
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd services/admin-ui && pnpm exec vitest run app/lib/__tests__/sessions.test.ts`
Expected: FAIL (`Failed to resolve import "../sessions"`).

- [ ] **Step 3: Create `lib/sessions.ts` and update importers**

Move into `lib/sessions.ts`, verbatim: from `generations.ts` the types and constants `formatTokens`, `SessionPurpose`, `SessionStatus`, `statusLabels`, `SessionFolder`, `SessionSummary`, `SessionDetail`, `SessionState`, `SessionEvent`, `HISTORY_PAGE`, `session()`, `getSession`, `sessionEventsUrl`, `getSessionHistory`, `sendMessage`, `stopSession`, `closeSession`, with `session = (id) => '/api/sessions/' + encodeURIComponent(id)`; from `agentSessions.ts` everything, with the interface changes in **Interfaces** above. Add:

```ts
const query = (values: Record<string, string | number | undefined>) => {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) if (value !== undefined && value !== '') params.set(key, String(value));
  const text = params.toString();
  return text ? `?${text}` : '';
};
export const getSessions = (filter: { purpose?: SessionPurpose; documentId?: string; offset?: number }, signal?: AbortSignal) =>
  request<{ sessions: RunSummary[] }>(`/api/sessions${query({ document_id: filter.documentId, purpose: filter.purpose, offset: filter.offset })}`, { signal });
export const getSessionOptions = (documentId: string | null, signal?: AbortSignal) =>
  request<AgentOptions>(`/api/sessions/options${query({ document_id: documentId ?? undefined })}`, { signal });
export const previewSession = (value: StartInput) =>
  request<{ config: Record<string, unknown>; first_message: string }>('/api/sessions/preview', json('POST', value));
export const startSession = (value: StartInput) => request<SessionDetail>('/api/sessions', json('POST', value));
export const sessionPath = (id: string) => `/sessions/${encodeURIComponent(id)}`;
```

`RunSummary` is `SessionSummary & { started_by: string | null; draft_id: string | null }`. In `SessionDetail` remove the sentence "(Generate documents)" from `document_draft`'s comment and nothing else.

`generations.ts` keeps `DEFAULT_SYSTEM_PROMPT`, `Selection`, `SessionInput`, `createSession`, `getGenerationContext` and imports `SessionDetail` from `./sessions`. Delete `getSessions` and `saveSessionDraft` there.

Update every importer (`grep -rln "lib/generations\|lib/agentSessions\|'./generations'\|'./agentSessions'" app`): `AgentDraftBar`, `AgentPane`, `AgentSettingsSheet`, `AgentTestView`, `DiscoveryRunView`, `DocumentAgentRuns`, `DocumentEditorView`, `DocumentGenerationForm`, `DraftView`, `GenerationView`, `GenerationsView`, `RunWorkspace`, `ServerPage`, `lib/agentSettings`, `lib/discovery`, `lib/drafts`, `lib/testSessions`, `lib/turns`, `lib/useSessionEvents`, and the tests `agentSessions.test.tsx`, `agentSettingsSheet.test.tsx`, `documentAgentRun.test.tsx`, `documentGenerationForm.test.tsx`, `operations.test.tsx`, `runWorkspace.test.tsx`, `lib/__tests__/agentOptionsFixture.ts`. Names: `getDocumentAgentRuns(id, signal)` → `getSessions({ documentId: id }, signal)`; `getAgentOptions(id, signal)` → `getSessionOptions(id, signal)`; `previewAgentSession` → `previewSession`; `startAgentSession` → `startSession`; `AgentRunSummary` → `RunSummary`; `GenerationsView`'s `getSessions(offset, signal)` → `getSessions({ purpose: 'documents', offset }, signal)`. In every test that stubs fetch, rewrite URLs: `/api/generations/sessions/` → `/api/sessions/`, `/api/generations/sessions?offset=` → `/api/sessions?purpose=documents&offset=`, `/api/agent-sessions/options?kind=revision&document_id=` → `/api/sessions/options?document_id=`, `/api/agent-sessions?document_id=` → `/api/sessions?document_id=`, `/api/agent-sessions/preview` → `/api/sessions/preview`, `POST /api/agent-sessions` → `POST /api/sessions`. `GenerationView` keeps compiling for now (Task 9 deletes it): drop its Save as draft button and `saveDraft`.

- [ ] **Step 4: Run the checks**

Run: `cd services/admin-ui && pnpm exec vitest run && pnpm exec tsc --noEmit`
Expected: the new test passes; failures remain only in the Ask agent tests that depend on the new start shape and fixture (Task 8): `agentSettings.test.ts`, `agentSettingsSheet.test.tsx`, `agentPane.test.tsx`. `tsc` may report errors only in `lib/agentSettings.ts`, `AgentSettingsSheet.tsx`, `AgentPane.tsx` and the fixture — fixed in Task 8. Anything else must pass.

- [ ] **Step 5: Commit**

```bash
git add -A services/admin-ui/app
git commit -m "refactor(admin-ui): one client module for runs, lib/sessions.ts, on /api/sessions"
```

---

### Task 8: Ask agent on the new start shape

**Files:**
- Modify: `services/admin-ui/app/lib/agentSettings.ts`, `app/components/AgentSettingsSheet.tsx`, `app/components/AgentPane.tsx`
- Modify: `services/admin-ui/app/lib/__tests__/agentOptionsFixture.ts`, `lib/__tests__/agentSettings.test.ts`, `components/__tests__/agentSettingsSheet.test.tsx`, `components/__tests__/agentPane.test.tsx`

**Interfaces:**
- Consumes: `~/lib/sessions` (Task 7).
- Produces: `startInput(documentId: string, documentTitle: string, options, settings, message): StartInput`; storage key unchanged, stored shape `{ v: 2, linked, settings }`; `restoreSettings` returns the defaults for anything not `v === 2`; `defaultSettings` picks `options.default_prompt_id` (its content) or, with no prompts, `promptKey = OWN` and an empty system prompt. `BUILT_IN` is renamed `OWN` (value `'own'`), meaning "my own text, no library prompt".

- [ ] **Step 1: Update the fixture and write the failing tests**

`agentOptionsFixture.ts`: drop `default_prompt` and `fixed_tools`; add `default_prompt_id: 'p2'`, a second prompt `{ id: 'p2', name: 'Revise a document', content: 'Read with get_document. Change with edit_document or save_document.' }`, `documents: [{ id: 'doc-1', name: 'Runbook', linked: true, editable: true }]` under `resources`, and the tools `{ name: 'save_document', label: 'Save a document', summary: 'Save the draft', group: 'documents', needs: 'editable_document', requires: 'a document to edit' }` and `{ name: 'get_document', label: 'Read document', summary: 'Read a document', group: 'inventory', needs: null, requires: null }`.

Add to `agentSettings.test.ts`:

```ts
test('the start body gives the document as an editable resource and the context names it', () => {
  const body = startInput('doc-1', 'Runbook', options, defaultSettings(options), '  Add a section.  ');
  expect(body).toMatchObject({ started_from: { document_id: 'doc-1' }, message: 'Add a section.', prompt_id: 'p2',
    system_prompt: options.prompts[1].content, context: 'Document: Runbook (id doc-1)' });
  expect(body.resources.documents).toEqual([{ id: 'doc-1', editable: true }]);
  expect(body.tools).toEqual(expect.arrayContaining(['save_document', 'get_document']));
  expect(body).not.toHaveProperty('kind');
});

test('settings saved before the update are dropped and rebuilt', () => {
  const old = { v: 1, linked: ['s1'], settings: { modelId: 'm1', promptKey: 'built-in', systemPrompt: 'Old', off: ['ssh_run'] } };
  expect(restoreSettings(options, old)).toEqual(defaultSettings(options));
});

test('with no library prompts the person writes their own', () => {
  const none = { ...options, prompts: [], default_prompt_id: null };
  expect(defaultSettings(none)).toMatchObject({ promptKey: OWN, systemPrompt: '' });
});
```

In `agentSettingsSheet.test.tsx` and `agentPane.test.tsx`: replace assertions on the fixed tools (`Read the document`, …) with `Save a document` being a ticked checkbox under Tools; add one sheet test that the Documents resource `Runbook` is checked and disabled; and pin the start request body in the pane's first-message test to `startInput('doc-1', 'Runbook', …)`'s output (the test already pins a body: change `kind`/`target`/`instruction` to `started_from`/`message`/`context` and add `resources.documents`). The Advanced prompt select lists `Careful reviser`, `Revise a document` and `Own prompt`.

- [ ] **Step 2: Run them to verify they fail**

Run: `cd services/admin-ui && pnpm exec vitest run app/lib/__tests__/agentSettings.test.ts app/components/__tests__/agentSettingsSheet.test.tsx app/components/__tests__/agentPane.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement**

`lib/agentSettings.ts`:
- imports from `./sessions`; `export const OWN = 'own';` replaces `BUILT_IN`.
- `defaultSettings`: `const first = options.prompts.find(item => item.id === options.default_prompt_id) ?? options.prompts[0];` then `promptKey: first ? first.id : OWN, systemPrompt: first ? first.content : ''`.
- `restoreSettings`: `if (v !== 2 || …) return base;` and `promptKey` accepts `OWN` or a known prompt id.
- `settingsFromSession`: `promptKey: … ? promptId : OWN`, `systemPrompt: session.prompt_snapshot?.system_prompt ?? defaultSettings(options).systemPrompt`.
- `startInput(documentId, documentTitle, options, settings, message)`:

```ts
export function startInput(documentId: string, documentTitle: string, options: AgentOptions, settings: AgentSettings, message: string): StartInput {
  return { started_from: { document_id: documentId }, message: message.trim(), model_id: settings.modelId,
    prompt_id: settings.promptKey === OWN ? null : settings.promptKey, system_prompt: settings.systemPrompt.trim(),
    context: `Document: ${documentTitle} (id ${documentId})`,
    resources: { ...settings.chosen, documents: options.resources.documents.map(item => ({ id: item.id, editable: item.editable })) },
    tools: shownTools(options, settings).filter(tool => !settings.off.includes(tool.name)).map(tool => tool.name),
    expert_ids: settings.experts, subagents: settings.subagents };
}
```

- the save effect writes `{ v: 2, linked: …, settings: … }`; `useAgentSettings` calls `getSessionOptions(documentId, signal)`.

`AgentSettingsSheet.tsx`:
- import `previewSession` from `~/lib/sessions` and `OWN` from `~/lib/agentSettings`; `runPreview` calls `previewSession(startInput(documentId, documentTitle, options, settings, previewMessage.trim() || '(your next message)'))`.
- `choosePrompt`: `systemPrompt: key === OWN ? settings.systemPrompt : options.prompts.find(item => item.id === key)?.content ?? ''`.
- Under "Servers and resources", before the KINDS loop, a Documents group:

```tsx
{options.resources.documents.length > 0 && <FieldSet><FieldLegend variant="label">Documents</FieldLegend>
  {options.resources.documents.map(item => <Field key={item.id} orientation="horizontal">
    <Checkbox id={`settings-document-${item.id}`} aria-label={item.name} checked disabled />
    <FieldLabel htmlFor={`settings-document-${item.id}`}>{item.name}</FieldLabel>
    <FieldDescription>{item.editable ? 'The agent may change it (as a draft).' : 'Read only.'}</FieldDescription></Field>)}
</FieldSet>}
```

- delete the `options.fixed_tools` block; the prompt select's last item is `<SelectItem value={OWN}>Own prompt</SelectItem>`.

`AgentPane.tsx`: imports from `~/lib/sessions`; `getSessions({ documentId }, controller.signal)`; `startSession(startInput(documentId, documentTitle, options, current, text))`; the `runs` state type `RunSummary[]`.

- [ ] **Step 4: Run the checks**

Run: `cd services/admin-ui && pnpm exec vitest run && pnpm exec tsc --noEmit`
Expected: PASS (the generation-page tests may still fail until Task 9 only if they reference `saveSessionDraft`; `generation.test.tsx` is replaced in Task 9 — if it fails here only on Save as draft, note it and continue).

- [ ] **Step 5: Commit**

```bash
git add -A services/admin-ui/app
git commit -m "feat(admin-ui): Ask agent starts through /api/sessions with the document as an editable resource"
```

---

### Task 9: One run page, `/sessions/:id`

**Files:**
- Create: `services/admin-ui/app/components/SessionView.tsx`, `app/routes/session.tsx`
- Delete: `app/components/GenerationView.tsx`, `app/routes/documentationGeneration.tsx`, `app/components/__tests__/generation.test.tsx`
- Modify: `app/routes.ts`, `app/components/GenerationsView.tsx`, `app/components/DocumentGenerationForm.tsx:231`, `app/components/DraftView.tsx:22-23`, `app/components/DiscoveryRunView.tsx:61`, `app/components/AdminLayout.tsx` (`sessionPage` matches `/sessions/`)
- Test: `app/components/__tests__/sessionView.test.tsx` (create); update `generations.test.tsx`, `documentGenerationForm.test.tsx`, `documentDrafts.test.tsx`, `discoveryRun.test.tsx` link expectations

**Interfaces:**
- Consumes: `sessionPath`, `getSession` & co. (Task 7), `useSessionEvents`, `RunWorkspace`, `agentRunPath`.
- Produces: `SessionView({ sessionId })`: any run on `RunWorkspace`; heading: title, `statusLabels[status] · model_name`, and "Started from" — a document link (`target_document_id` → `agentRunPath(document, id)`, label "the document"), a server (`server_id` → `/servers/{id}/discovery/{runId}`), an agent (`agent_id` → `/agents/{id}?view=test&session_id={runId}`), or nothing; the draft section (as `GenerationView`'s, without Save as draft) only when the run has a `draft` text; header action Close; composer as `GenerationView`'s. No redirects.

- [ ] **Step 1: Write the failing test** (`sessionView.test.tsx`, following the fetch-stub pattern of the existing `generation.test.tsx`, which this replaces — copy its `api()` helper, `EventSource` stub and session fixture)

```tsx
test('a document run shows where it started, with no Save as draft', async () => {
  serve({ ...detail, purpose: 'revision', target_document_id: 'doc-1', draft: null, document_draft: null });
  render(<MemoryRouter initialEntries={['/sessions/s1']}><Routes><Route path="/sessions/:id" element={<SessionView sessionId="s1" />} /></Routes></MemoryRouter>);
  expect(await screen.findByRole('heading', { name: detail.title })).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'the document' })).toHaveAttribute('href', '/documents/doc-1/agent/s1');
  expect(screen.queryByRole('button', { name: 'Save as draft' })).toBeNull();
});

test('a Generate documents run shows its draft and opens the document draft', async () => {
  serve({ ...detail, purpose: 'documents', draft: '# Draft', draft_turn: 1, document_draft: { id: 'x1', document_id: null, status: 'open' } });
  render(<MemoryRouter><SessionView sessionId="s1" /></MemoryRouter>);
  expect(await screen.findByRole('link', { name: 'Open draft' })).toHaveAttribute('href', '/documents/drafts/x1');
});

test('a Test run is shown here, not redirected, and links to its agent', async () => {
  serve({ ...detail, purpose: 'test', agent_id: 'code', draft: null });
  render(<MemoryRouter><SessionView sessionId="s1" /></MemoryRouter>);
  expect(await screen.findByRole('link', { name: 'the agent' })).toHaveAttribute('href', '/agents/code?view=test&session_id=s1');
});

test('a refused tool call shows as an error on that call', async () => {
  serve({ ...detail, purpose: 'revision', target_document_id: 'doc-1' }, [toolCall('save_document'),
    toolResult('save_document', 'This document is not available in this run.', true)]);
  render(<MemoryRouter><SessionView sessionId="s1" /></MemoryRouter>);
  expect(await screen.findByText(/not available in this run/)).toBeInTheDocument();
});
```

`serve(session, events = [])` stubs `GET /api/sessions/s1` with the session and `GET /api/sessions/s1/events/history?limit=100` with `{ events, has_more: false }`; `toolCall`/`toolResult` build `SessionEvent`s the way `runWorkspace.test.tsx` builds them (copy its helpers).

- [ ] **Step 2: Run it to verify it fails**

Run: `cd services/admin-ui && pnpm exec vitest run app/components/__tests__/sessionView.test.tsx`
Expected: FAIL (`Failed to resolve import "../SessionView"`).

- [ ] **Step 3: Implement**

`SessionView.tsx`: copy `GenerationView.tsx`, rename the component and docstring ("Any run on the run workspace: its turns, messages, Stop and Close; where it was started; and, for a run that writes a document draft, the draft."), delete the three `<Navigate>` redirects and the `Navigate` import, and replace `heading` with:

```tsx
heading={<>
  <h1 className="break-words text-2xl font-semibold">{session.title}</h1>
  <p className="text-sm text-muted-foreground">{statusLabels[session.status]} · {session.model_name}
    {startedFrom && <> · Started from {startedFrom}</>}</p>
</>}
```

with

```tsx
const startedFrom = session.target_document_id
  ? <Link className="underline" to={agentRunPath(session.target_document_id, session.id)}>the document</Link>
  : session.server_id
    ? <Link className="underline" to={`/servers/${encodeURIComponent(session.server_id)}/discovery/${encodeURIComponent(session.id)}`}>the server</Link>
    : session.agent_id
      ? <Link className="underline" to={`/agents/${encodeURIComponent(session.agent_id)}?view=test&session_id=${encodeURIComponent(session.id)}`}>the agent</Link>
      : session.purpose === 'documents' ? <Link className="underline" to="/documentation/generations">Generate documents</Link> : null;
```

Render the `sidebar` draft section only when `session.purpose === 'documents'` (the runs that keep a draft text). `resources={session.run_resources ? 'Per run' : 'Fixed'}` stays.

`routes/session.tsx`:

```tsx
import { useParams } from 'react-router';
import AdminLayout from '../components/AdminLayout';
import SessionView from '../components/SessionView';

export default function Page() {
  const { sessionId } = useParams();
  return <AdminLayout><SessionView key={sessionId} sessionId={sessionId!} /></AdminLayout>;
}
```

`routes.ts`: replace `route("documentation/generations/:sessionId", "./routes/documentationGeneration.tsx")` with `route("sessions/:sessionId", "./routes/session.tsx")`.
Links: `GenerationsView` row link and `DocumentGenerationForm`'s `navigate` → `sessionPath(id)`; `DraftView`'s `DraftAuthor` → `draft.session_purpose === 'revision' && draft.document_id ? agentRunPath(…) : sessionPath(draft.session_id)`; `DiscoveryRunView:61` → `<Navigate replace to={sessionPath(runId)} />`. `AdminLayout`: `sessionPage` also matches `pathname.startsWith('/sessions/')` (the run's own heading is the title) and the old `/documentation/generations/(?!new$)…` pattern is removed.
Delete `GenerationView.tsx`, `routes/documentationGeneration.tsx` and `__tests__/generation.test.tsx`; move any of its tests that are not about redirects or Save as draft into `sessionView.test.tsx`.

- [ ] **Step 4: Update link expectations and run all checks**

`grep -rn "documentation/generations/" app` must show only `routes.ts` (`documentation/generations/new`), `GenerationsView`'s New button and tests asserting the New button. Update the other test expectations to `/sessions/…`.
Run: `cd services/admin-ui && pnpm exec vitest run && pnpm exec tsc --noEmit && pnpm exec react-router routes`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A services/admin-ui/app
git commit -m "feat(admin-ui): one run page, /sessions/:id, for every run; links go there"
```

---

### Task 10: Docs and the final search

**Files:**
- Modify: `docs/user-guide.md` ("#### Ask agent", the Generate documents run page paragraph, the Agent runs mentions), `docs/agents.md` (session API paths, the document tools), `docs/documentation.md` (the UI routes line and the Ask agent paragraph), `deploy/README.md:363` (the session routes line), `services/admin-api/src/knowledge_admin/tools/README.md` if it names the old tools.

- [ ] **Step 1: Update the docs**

- Every `/api/generations/sessions/{id}…` → `/api/sessions/{id}…`; `/api/agent-sessions…` → `/api/sessions` (options, preview, start); `/documentation/generations/:id` → `/sessions/:id`.
- The document tools: `get_document`, `edit_document`, `save_document`, each taking `document_id`; a run may change only documents it was given as editable; the editing rules are the **Revise a document** prompt in Prompts, which you can open and change.
- User guide "#### Ask agent": add one sentence — "How the agent works on the document is the **Revise a document** prompt (Administration → Prompts); change it there, or pick another prompt under Advanced." Remove any mention of Save as draft.

- [ ] **Step 2: Final search**

Run from the worktree root:

```bash
git grep -n "agent-sessions\|api/generations/sessions/\|agent_launch\|revision_run\|get_doc\b\|save_doc\b\|edit_doc\b\|saveSessionDraft\|GenerationView\|agentSessions'\|fixed_tools\|DEFAULT_REVISION_PROMPT" -- . ':!docs/superpowers' ':!db/migrations/0097*' ':!db/migrations/0099*'
```

Expected: only `savedIn`'s list of old tool names in `lib/sessions.ts` (with its comment) and the migration 0100 comment.

- [ ] **Step 3: Run everything**

Run: `cd services/admin-api && uv run --frozen pytest -q -p no:warnings` and `cd services/admin-ui && pnpm exec vitest run && pnpm exec tsc --noEmit && pnpm exec react-router routes`
Expected: PASS apart from the failures named in Global Constraints.

- [ ] **Step 4: Commit**

```bash
git add docs deploy services/admin-api/src/knowledge_admin/tools/README.md
git commit -m "docs: one sessions API, document tools and the Revise a document prompt"
```
