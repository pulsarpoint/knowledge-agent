# Reader Chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reader users get cited, docs-only answers from an admin-configured agent, with gaps logged and conversations reviewable in admin.

**Architecture:** docs-wiki becomes a thin adapter that forwards Reader chat to new internal admin-api endpoints. Those endpoints reuse the admin Test-tab turn service (extracted into `start_turn`) and the generic `agents.agent()` runner. Grounding is agent configuration (`documents.grounded`), and Reader conversations are live sessions in `ka.agent_sessions` with `channel = 'reader'`.

**Tech Stack:** Python 3.12, FastAPI, psycopg 3, httpx, openai-agents 0.19.2, PostgreSQL 17 (golang-migrate), React Router 7 + Vitest, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-10-02-reader-chat-design.md`

### Deliberate deviations from the spec (approved scope, simpler mechanics)

1. **SSE instead of NDJSON** on both hops. The admin turn service already streams SSE and the Reader already ships `app/lib/sse.ts`; one format, no new parser.
2. **`ka.agent_sessions.channel`** (`'admin' | 'reader'`) is added in migration 0060. Reader listings and Activity filter on it, so an admin's own live sessions never appear in their Reader history, and Reader history survives switching the Reader agent.
3. **Grounded agents receive only citable document tools** (`search_documents`, `read_document`, `report_not_covered`); the raw `list_files/read_file/search_files/shell_run` tools are added only when Code is enabled or Documents is enabled without grounding. Otherwise uncited file reads could bypass citations.
4. **Activity** lists all `channel = 'reader'` sessions (any agent) rather than only the current Reader agent's.

## Global Constraints

- No new third-party dependencies in any package.
- `agents.agent()` and `agents.create_agent()` signatures stay unchanged.
- Admin `POST /api/agents/{id}/runs` behavior and SSE output stay unchanged; all existing tests in `services/admin-api/tests/test_agent_runs.py` and `test_agent_sessions.py` stay green.
- Internal endpoints live under `/internal/reader`, never under `/api`.
- Reader session idle timeout: 7 days. Admin sessions: 1 hour.
- Reader run limits: question 1–10000 characters; the runner's defaults of 20 turns and 300 s apply.
- User-facing strings (exact):
  - `The assistant isn't configured yet. Ask an administrator.`
  - `The assistant's configuration isn't allowed in the Reader.`
  - `Chat is temporarily unavailable.`
  - `Wait for the current answer.`
  - `This conversation has ended. Start a new one.`
  - `Unknown conversation`
  - `This agent is the Reader assistant. Choose another Reader agent first.`
- Secrets never reach the browser, the Reader, or logs; docs-wiki forwards no gateway headers upstream.
- Python test command pattern: `cd services/<service> && uv run --frozen pytest <file> -q` (admin-api tests need the image from `make db-test-image`).
- UI test command pattern: `cd services/<ui> && pnpm exec vitest run <file>`.

## Review Focus

1. **Agent with Documents on but zero usable folders** (none selected, or the user's Advanced selection excludes all of them): searches must return nothing and never fall back to "all folders". Test: Task 3 `test_empty_folder_scope_never_searches_everything`.
2. **User selects folders the agent is not allowed to use**: the intersection is empty; no broadening. Test: Task 6 `test_narrow_keeps_only_intersection`.
3. **Answer cites numbers that were never returned, or `[n]` inside code**: only real excerpt numbers become sources. Test: Task 3 `test_result_lists_only_cited_existing_excerpts`.
4. **Admin switches the Reader agent while a user's conversation is open**: the conversation continues with its pinned configuration (still tier-checked) and stays in the user's list. Test: Task 6 `test_switching_reader_agent_keeps_existing_conversations`.
5. **admin-api restarts mid-answer** (stream ends without `completed`): the Reader shows `Chat is temporarily unavailable.` instead of hanging, and logs no gap. Test: Task 9 `test_stream_without_completion_reports_unavailable`.

---

### Task 1: Migration 0060_reader_assistant

**Files:**
- Create: `db/migrations/0060_reader_assistant.up.sql`
- Create: `db/migrations/0060_reader_assistant.down.sql`
- Test: `services/admin-api/tests/test_reader_migration.py`

**Interfaces:**
- Produces: columns `ka.agents.documents_grounded boolean`, `ka.agent_sessions.idle_timeout interval`, `ka.agent_sessions.hidden_by_owner_at timestamptz`, `ka.agent_sessions.channel text`; table `ka.reader_settings(singleton, knowledge_agent_id, revision, updated_by, updated_at)`.

- [ ] **Step 1: Write the failing test**

```python
"""Migration 0060 adds grounding, reader sessions and the Reader assistant setting."""
from pathlib import Path

import psycopg
import pytest

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'


def test_reader_assistant_schema(store):
    with store.connect() as conn:
        grounded = conn.execute("SELECT id FROM ka.agents WHERE documents_grounded ORDER BY id").fetchall()
        assert [row['id'] for row in grounded] == ['search']
        assert conn.execute('SELECT knowledge_agent_id, revision FROM ka.reader_settings').fetchone() == {
            'knowledge_agent_id': 'search', 'revision': 1}
        column = conn.execute("""SELECT column_default FROM information_schema.columns
            WHERE table_schema='ka' AND table_name='agent_sessions' AND column_name='channel'""").fetchone()
        assert column['column_default'] == "'admin'::text"


def test_reader_settings_row_is_a_singleton_and_blocks_agent_delete(store):
    with store.connect() as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("INSERT INTO ka.reader_settings(singleton, knowledge_agent_id) VALUES (false, 'search')")
    with store.connect() as conn:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            conn.execute("UPDATE ka.reader_settings SET knowledge_agent_id='missing'")


def test_down_then_up_is_clean(store):
    down = (MIGRATIONS / '0060_reader_assistant.down.sql').read_text()
    up = (MIGRATIONS / '0060_reader_assistant.up.sql').read_text()
    with store.connect() as conn:
        conn.execute(down, prepare=False)
        assert conn.execute("SELECT to_regclass('ka.reader_settings') AS t").fetchone()['t'] is None
        conn.execute(up, prepare=False)
        assert conn.execute('SELECT count(*) AS n FROM ka.reader_settings').fetchone()['n'] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_reader_migration.py -q`
Expected: FAIL — `column "documents_grounded" does not exist`.

- [ ] **Step 3: Write the migrations**

`db/migrations/0060_reader_assistant.up.sql`:

```sql
SET search_path = ka, public;

ALTER TABLE agents ADD COLUMN documents_grounded boolean NOT NULL DEFAULT false;
UPDATE agents SET documents_grounded = true WHERE id = 'search';

ALTER TABLE agent_sessions
    ADD COLUMN idle_timeout interval NOT NULL DEFAULT interval '1 hour' CHECK (idle_timeout > interval '0'),
    ADD COLUMN hidden_by_owner_at timestamptz,
    ADD COLUMN channel text NOT NULL DEFAULT 'admin' CHECK (channel IN ('admin', 'reader'));
CREATE INDEX agent_sessions_reader_owner ON agent_sessions (actor_id, last_activity_at DESC)
    WHERE channel = 'reader';

CREATE TABLE reader_settings (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    knowledge_agent_id text NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    updated_by uuid REFERENCES principals(id),
    updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO reader_settings(knowledge_agent_id) SELECT id FROM agents WHERE id = 'search';
```

`db/migrations/0060_reader_assistant.down.sql`:

```sql
SET search_path = ka, public;

DROP TABLE reader_settings;
DROP INDEX agent_sessions_reader_owner;
ALTER TABLE agent_sessions DROP COLUMN channel, DROP COLUMN hidden_by_owner_at, DROP COLUMN idle_timeout;
ALTER TABLE agents DROP COLUMN documents_grounded;
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_reader_migration.py -q`
Expected: 3 passed.

- [ ] **Step 5: Run the full DB suite**

Run: `make db-test`
Expected: exits 0 (the suite migrates to the latest version).

- [ ] **Step 6: Commit**

```bash
git add db/migrations/0060_reader_assistant.*.sql services/admin-api/tests/test_reader_migration.py
git commit -m "feat(db): reader assistant settings, grounded documents and reader sessions"
```

---

### Task 2: "Answer only from documents" configuration

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/agents.py` (`get_agent_config`: SELECT list and the `'documents'` dict)
- Modify: `services/admin-api/src/knowledge_admin/profiles_api.py` (`ProfileInput`, `update`)
- Test: `services/admin-api/tests/test_profiles.py` (append)

**Interfaces:**
- Consumes: `ka.agents.documents_grounded` (Task 1).
- Produces: `config['documents']['grounded']: bool` from `get_agent_config`; `PUT /api/agents/{id}` accepts optional `documents_grounded: bool` (omitted = keep); `GET /api/agents/{id}` returns `documents_grounded`. Readers of older version snapshots must use `config['documents'].get('grounded', False)`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_profiles.py`)

```python
def test_documents_grounded_is_saved_versioned_and_kept_when_omitted(store, tmp_path, monkeypatch):
    monkeypatch.setenv('CODE_ROOT', str(tmp_path.resolve()))
    monkeypatch.setenv('RAG_SOURCE_ROOT', str(tmp_path.resolve()))
    client = _client(tmp_path, repository_store=store)
    agent = create(client, 'Grounded docs')
    saved = client.get(f"/api/agents/{agent['id']}", headers=HEADERS).json()
    assert saved['documents_grounded'] is False
    payload = {key: saved[key] for key in ('revision', 'name', 'description', 'when_to_use', 'enabled', 'all_folders',
        'all_repositories', 'include_subfolders', 'folder_ids', 'repository_ids', 'service_ids', 'documents_enabled',
        'repositories_enabled', 'ssh_enabled', 'skills_enabled', 'mcp_enabled', 'services_enabled')}
    updated = client.put(f"/api/agents/{agent['id']}", headers=HEADERS,
                         json={**payload, 'documents_enabled': True, 'documents_grounded': True})
    assert updated.status_code == 200, updated.text
    assert updated.json()['documents_grounded'] is True
    kept = client.put(f"/api/agents/{agent['id']}", headers=HEADERS,
                      json={**payload, 'revision': updated.json()['revision'], 'documents_enabled': True})
    assert kept.json()['documents_grounded'] is True
    config = client.get(f"/api/agents/{agent['id']}/config", headers=HEADERS).json()
    assert config['documents']['grounded'] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_profiles.py -k documents_grounded -q`
Expected: FAIL — 400 `Extra inputs are not permitted` for `documents_grounded`.

- [ ] **Step 3: Implement**

In `agents.py` `get_agent_config`, add `documents_grounded` to the agent SELECT column list (after `documents_enabled`) and change the returned `'documents'` entry to:

```python
            'documents': {'enabled': agent['documents_enabled'], 'grounded': agent['documents_grounded'],
                'all_folders': agent['all_folders'], 'include_subfolders': agent['include_subfolders'], 'folders': folders},
```

In `profiles_api.py` add to `ProfileInput` (after `documents_enabled`):

```python
    documents_grounded: bool | None = Field(default=None, strict=True)
```

In `update`, extend the coordinator guard tuple with `value.documents_grounded`, then before the final `UPDATE ka.agents` add:

```python
                grounded = old['documents_grounded'] if value.documents_grounded is None else value.documents_grounded
```

and change the UPDATE to also set `documents_grounded=%s`, passing `grounded` right after `value.documents_enabled`:

```python
                conn.execute('''UPDATE ka.agents SET name=%s,description=%s,when_to_use=%s,enabled=%s,all_folders=%s,
                    all_repositories=%s,include_subfolders=%s,documents_enabled=%s,documents_grounded=%s,repositories_enabled=%s,ssh_enabled=%s,skills_enabled=%s,mcp_enabled=%s,services_enabled=%s,revision=revision+1,updated_by=%s WHERE id=%s''',
                    (value.name,value.description,value.when_to_use,value.enabled,value.all_folders,value.all_repositories,value.include_subfolders,value.documents_enabled,grounded,value.repositories_enabled,value.ssh_enabled,value.skills_enabled,value.mcp_enabled,value.services_enabled,actor,agent_id))
```

`read()` already returns every `ka.agents` column through `knowledge_llm.profiles.profile` (`SELECT *`).

- [ ] **Step 4: Run tests**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_profiles.py tests/test_agent_versions.py tests/test_agents.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/agents.py services/admin-api/src/knowledge_admin/profiles_api.py services/admin-api/tests/test_profiles.py
git commit -m "feat(agents): answer only from documents setting"
```

---

### Task 3: Document tools, grounding result, prompt and tool selection

**Files:**
- Create: `services/admin-api/src/knowledge_admin/tools/documents.py`
- Create: `services/admin-api/src/knowledge_admin/grounding.py`
- Modify: `services/admin-api/src/knowledge_admin/agents.py` (`get_tools`, `get_prompt`)
- Test: `services/admin-api/tests/test_document_tools.py`

**Interfaces:**
- Consumes: `config['documents']` with `enabled`, `grounded`, `all_folders`, `folders[].search_folder` (dotted relative path).
- Produces:
  - tools `search_documents(query: str, folder: str | None = None)`, `read_document(path: str)`, `report_not_covered(reason: str)` in `knowledge_admin.tools.documents`;
  - run-context entry `context['grounding'] = {'excerpts': [{'n', 'path', 'folder', 'text'}], 'searches': int, 'empty_searches': int, 'reported': str | None}`;
  - `knowledge_admin.grounding.result(answer: str, state: dict | None) -> dict | None` returning `{'sources': [{'n', 'path', 'heading_path', 'anchor': None}], 'not_covered': bool, 'near_misses': [{'path', 'heading_path'}], 'results': int, 'reason': str | None}`;
  - `knowledge_admin.grounding.POLICY: str`;
  - module variable `knowledge_admin.tools.documents.RAG_TRANSPORT` (tests replace it).

- [ ] **Step 1: Write the failing tests**

```python
"""Document tools search only the agent's folders and record citable excerpts."""
import asyncio
import json

import httpx
import pytest
from agents.tool_context import ToolContext

from knowledge_admin import grounding
from knowledge_admin.agents import get_prompt, get_tools
from knowledge_admin.tools import documents
from knowledge_admin.tools.documents import read_document, report_not_covered, search_documents


@pytest.fixture
def rag(monkeypatch):
    calls = []
    state = {'hits': [{'path': 'payments/retries.md', 'folder': 'payments', 'text': 'Retries stop after 3 attempts.'}],
             'fail': False}

    def handle(request):
        calls.append(request)
        if state['fail']:
            return httpx.Response(503, json={'error': {'message': 'down'}})
        if request.url.path == '/backoffice/workspace':
            return httpx.Response(200, json={'project_id': 'p1'})
        if request.url.path == '/search':
            return httpx.Response(200, json={'results': state['hits']})
        if request.url.path == '/backoffice/projects/p1/file':
            return httpx.Response(200, json={'content': 'Full retry policy.'})
        raise AssertionError(request.url)

    monkeypatch.setenv('DOCS_WIKI_RAG_URL', 'http://rag.test')
    monkeypatch.setenv('RAG_BACKOFFICE_KEY', 'rag-key')
    monkeypatch.setattr(documents, 'RAG_TRANSPORT', httpx.MockTransport(handle))
    return calls, state


def config(**documents_settings):
    base = {'enabled': True, 'grounded': True, 'all_folders': False,
            'folders': [{'path': '/work/rag-data/payments', 'search_folder': 'payments'}]}
    return {'enabled': True, 'name': 'Docs', 'description': '', 'instructions': '',
            'system_prompt': {'content': 'Answer questions.'},
            'documents': {**base, **documents_settings},
            'code': {'enabled': False, 'projects': []}, 'skills': {'enabled': False, 'items': []},
            'services': {'enabled': False, 'items': []}, 'ssh': {'enabled': False, 'servers': []},
            'mcp': {'enabled': False, 'servers': []}}


def invoke(tool, context, **arguments):
    payload = json.dumps(arguments)
    wrapped = ToolContext(context=context, tool_name=tool.name, tool_call_id='t1', tool_arguments=payload)
    return asyncio.run(tool.on_invoke_tool(wrapped, payload))


def run_context(cfg):
    return {'config': cfg, 'credentials': {}, 'workspace': None, 'shell_enabled': False}


def test_search_numbers_excerpts_and_limits_folders(rag):
    calls, _ = rag
    context = run_context(config())
    output = invoke(search_documents, context, query='retry limit')
    assert output.startswith('[1] payments/retries.md (payments)\nRetries stop after 3 attempts.')
    body = json.loads(calls[-1].content)
    assert body['folders'] == ['payments'] and body['mode'] == 'hybrid' and body['project_id'] == 'p1'
    assert calls[-1].headers['x-rag-key'] == 'rag-key'
    assert context['grounding']['searches'] == 1 and context['grounding']['excerpts'][0]['n'] == 1


def test_empty_folder_scope_never_searches_everything(rag):
    calls, _ = rag
    context = run_context(config(folders=[]))
    assert invoke(search_documents, context, query='anything') == 'No matches in the documentation.'
    assert calls == []
    assert context['grounding'] == {'excerpts': [], 'searches': 1, 'empty_searches': 1, 'reported': None}


def test_folder_argument_must_be_an_allowed_folder(rag):
    context = run_context(config())
    assert 'outside' in invoke(search_documents, context, query='x', folder='people')


def test_all_folders_searches_everything(rag):
    calls, _ = rag
    invoke(search_documents, run_context(config(all_folders=True)), query='x')
    assert 'folders' not in json.loads(calls[-1].content)


def test_rag_failure_is_reported_to_model_and_not_counted(rag):
    _, state = rag
    state['fail'] = True
    context = run_context(config())
    assert 'unavailable' in invoke(search_documents, context, query='x')
    assert context.get('grounding', {}).get('searches', 0) == 0


def test_read_document_registers_excerpt_and_checks_folder(rag):
    context = run_context(config())
    assert invoke(read_document, context, path='payments/retries.md') == '[1] payments/retries.md\nFull retry policy.'
    assert 'outside' in invoke(read_document, context, path='people/salaries.md')
    assert 'Use a document path' in invoke(read_document, context, path='../etc/passwd')


def test_report_not_covered_records_reason(rag):
    context = run_context(config())
    invoke(report_not_covered, context, reason='No refund SLA documented.')
    assert context['grounding']['reported'] == 'No refund SLA documented.'


def test_result_lists_only_cited_existing_excerpts():
    state = {'excerpts': [{'n': 1, 'path': 'a/x.md', 'folder': 'a', 'text': 't'},
                          {'n': 2, 'path': 'a/y.md', 'folder': 'a', 'text': 't'}],
             'searches': 1, 'empty_searches': 0, 'reported': None}
    summary = grounding.result('Use x [2][2], not [7].\n```\ncode [1]\n```', state)
    assert summary['sources'] == [{'n': 2, 'path': 'a/y.md', 'heading_path': 'a', 'anchor': None}]
    assert summary['not_covered'] is False and summary['results'] == 2
    assert grounding.result('hello', None) is None


@pytest.mark.parametrize('state, expected', [
    ({'excerpts': [], 'searches': 2, 'empty_searches': 2, 'reported': None}, True),
    ({'excerpts': [], 'searches': 0, 'empty_searches': 0, 'reported': None}, False),
    ({'excerpts': [{'n': 1, 'path': 'a/x.md', 'folder': 'a', 'text': 't'}], 'searches': 1, 'empty_searches': 0, 'reported': 'gap'}, True),
])
def test_not_covered_rule(state, expected):
    assert grounding.result('answer', state)['not_covered'] is expected


def test_grounded_agents_get_only_citable_tools_and_policy():
    grounded = config()
    names = {tool.name for tool in get_tools(grounded, [])}
    assert names == {'search_documents', 'read_document', 'report_not_covered'}
    assert grounding.POLICY in get_prompt(grounded, [], [])
    ungrounded = config(grounded=False)
    names = {tool.name for tool in get_tools(ungrounded, [])}
    assert {'search_documents', 'read_document', 'list_files', 'shell_run'} <= names
    assert 'report_not_covered' not in names
    assert grounding.POLICY not in get_prompt(ungrounded, [], [])
```

Note on the code-block case in `test_result_lists_only_cited_existing_excerpts`: `[1]` appears only inside a fenced block, so it must not be cited.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_document_tools.py -q`
Expected: FAIL — `ModuleNotFoundError: knowledge_admin.grounding`.

- [ ] **Step 3: Implement `grounding.py`**

```python
"""Turn a grounded run's recorded excerpts into cited sources and a coverage decision."""
import re
from typing import Any

CITATION = re.compile(r'\[(\d{1,4})\]')
FENCE = re.compile(r'```.*?```|~~~.*?~~~|`[^`\n]*`', re.DOTALL)

POLICY = (
    '\nAnswer only from excerpts returned by search_documents or read_document in this conversation. '
    'Search before answering any factual question. Cite every claim with its excerpt number in square brackets, such as [2]. '
    'Never cite a number that a tool did not return. Never use general knowledge or assumptions for factual claims. '
    'When the excerpts do not answer the question, call report_not_covered with what is missing, then tell the user '
    'that the documentation does not cover it and what is missing.')


def empty_state() -> dict[str, Any]:
    return {'excerpts': [], 'searches': 0, 'empty_searches': 0, 'reported': None}


def result(answer: str, state: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the cited sources and coverage of a grounded run, or None for other runs."""
    if state is None:
        return None
    by_number = {excerpt['n']: excerpt for excerpt in state['excerpts']}
    cited = sorted({int(number) for number in CITATION.findall(FENCE.sub('', answer))} & by_number.keys())
    sources = [{'n': n, 'path': by_number[n]['path'], 'heading_path': by_number[n]['folder'], 'anchor': None}
               for n in cited]
    searched_nothing = state['searches'] > 0 and state['empty_searches'] == state['searches']
    return {'sources': sources, 'not_covered': state['reported'] is not None or searched_nothing,
            'near_misses': [{'path': e['path'], 'heading_path': e['folder']} for e in state['excerpts'][:3]],
            'results': len(state['excerpts']), 'reason': state['reported']}
```

- [ ] **Step 4: Implement `tools/documents.py`**

```python
"""Search and read the RAG knowledge base within the agent's document folders, recording citable excerpts."""
import os
from pathlib import PurePosixPath
from typing import Any

import httpx
from agents import RunContextWrapper, function_tool

from knowledge_admin.agent_activity import redact
from knowledge_admin.grounding import empty_state
from knowledge_admin.tools import AgentContext

RAG_TRANSPORT: httpx.AsyncBaseTransport | None = None
MAX_DOCUMENT_BYTES = 65536
MAX_FOLDER_SELECTORS = 200


def _state(context: AgentContext) -> dict[str, Any]:
    return context.setdefault('grounding', empty_state())


def _allowed(config: dict[str, Any]) -> list[str] | None:
    """Relative folder paths the agent may use; None means every folder."""
    documents = config['documents']
    if not config['enabled'] or not documents['enabled']:
        raise ValueError('Document search is not enabled for this agent.')
    if documents.get('all_folders'):
        return None
    return sorted({folder['search_folder'].replace('.', '/') for folder in documents['folders']})


def _client() -> httpx.AsyncClient:
    base, key = os.environ.get('DOCS_WIKI_RAG_URL'), os.environ.get('RAG_BACKOFFICE_KEY')
    if not base or not key:
        raise ValueError('Document search is not configured.')
    return httpx.AsyncClient(base_url=base, headers={'X-Rag-Key': key}, timeout=30, transport=RAG_TRANSPORT)


async def _project(client: httpx.AsyncClient) -> str:
    response = await client.get('/backoffice/workspace')
    response.raise_for_status()
    return response.json()['project_id']


def _register(state: dict[str, Any], path: str, folder: str, text: str) -> int:
    number = len(state['excerpts']) + 1
    state['excerpts'].append({'n': number, 'path': path, 'folder': folder, 'text': text})
    return number


@function_tool
async def search_documents(ctx: RunContextWrapper[AgentContext], query: str, folder: str | None = None) -> str:
    """Search the knowledge base and return numbered excerpts to cite as [n].

    Args:
        query: Words or a question to search for.
        folder: Optional document folder such as "payments/runbooks" to narrow the search.
    """
    context = ctx.context
    allowed = _allowed(context['config'])
    if folder is not None:
        folder = folder.strip().strip('/')
        if allowed is not None and folder not in allowed:
            return 'This folder is outside the agent’s document folders.'
        selectors = [folder.replace('/', '.')]
    else:
        selectors = None if allowed is None else [path.replace('/', '.') for path in allowed]
    state = _state(context)
    if selectors == []:
        state['searches'] += 1
        state['empty_searches'] += 1
        return 'No matches in the documentation.'
    if selectors is not None and len(selectors) > MAX_FOLDER_SELECTORS:
        return 'Too many document folders are selected. Ask an administrator to narrow this agent’s folders.'
    try:
        async with _client() as client:
            body = {'project_id': await _project(client), 'query': query[:4000], 'mode': 'hybrid', 'top': 8}
            if selectors is not None:
                body['folders'] = selectors
            response = await client.post('/search', json=body)
            response.raise_for_status()
            hits = response.json()['results']
    except (httpx.HTTPError, KeyError, ValueError):
        return 'Document search is unavailable right now. Try again, or tell the user the knowledge base could not be searched.'
    state['searches'] += 1
    if not hits:
        state['empty_searches'] += 1
        return 'No matches in the documentation.'
    secrets = context['credentials'].values()
    blocks = []
    for hit in hits:
        text = redact(hit['text'], secrets)
        number = _register(state, hit['path'], hit['folder'], text)
        blocks.append(f"[{number}] {hit['path']} ({hit['folder']})\n{text}")
    return '\n\n'.join(blocks)


@function_tool
async def read_document(ctx: RunContextWrapper[AgentContext], path: str) -> str:
    """Read one knowledge-base document returned by search_documents; cite it with its [n].

    Args:
        path: Document path exactly as returned by search_documents, such as "payments/retries.md".
    """
    context = ctx.context
    allowed = _allowed(context['config'])
    path = path.strip()
    parts = path.split('/')
    if not path or path.startswith('/') or '\\' in path or len(parts) < 2 or any(part in ('', '.', '..') for part in parts):
        return 'Use a document path returned by search_documents.'
    folder = str(PurePosixPath(path).parent)
    if allowed is not None and folder not in allowed:
        return 'This document is outside the agent’s document folders.'
    try:
        async with _client() as client:
            project = await _project(client)
            response = await client.get(f'/backoffice/projects/{project}/file', params={'path': path})
            if response.status_code == 404:
                return 'Document not found.'
            response.raise_for_status()
            content = response.json()['content']
    except (httpx.HTTPError, KeyError, ValueError):
        return 'The document could not be read right now.'
    encoded = content.encode()
    text = redact(encoded[:MAX_DOCUMENT_BYTES].decode(errors='ignore'), context['credentials'].values())
    number = _register(_state(context), path, folder.replace('/', '.'), text)
    return f'[{number}] {path}\n{text}' + ('\n[truncated]' if len(encoded) > MAX_DOCUMENT_BYTES else '')


@function_tool
async def report_not_covered(ctx: RunContextWrapper[AgentContext], reason: str) -> str:
    """Record that the retrieved documents do not answer the question.

    Args:
        reason: What information is missing from the documentation.
    """
    _state(ctx.context)['reported'] = reason.strip()[:1000] or 'Not covered by the documentation.'
    return 'Recorded. Tell the user the documentation does not cover this and what is missing.'
```

- [ ] **Step 5: Wire tools and policy into `agents.py`**

Add imports:

```python
from knowledge_admin import grounding
from knowledge_admin.tools.documents import read_document, report_not_covered, search_documents
```

Add helper above `get_prompt`:

```python
def documents_grounded(config: dict[str, Any]) -> bool:
    return config['documents']['enabled'] and config['documents'].get('grounded', False)
```

In `get_prompt`, change the shell-guidance condition `if config['code']['enabled'] or config['documents']['enabled']:` to:

```python
    if config['code']['enabled'] or config['documents']['enabled'] and not documents_grounded(config):
```

and, right after the `if config['documents']['enabled']: resources['documents'] = ...` block, add:

```python
    if config['documents']['enabled']:
        prompt += '\nUse search_documents to find knowledge-base excerpts and read_document for a whole document.'
    if documents_grounded(config):
        prompt += grounding.POLICY
```

Replace `get_tools` with:

```python
def get_tools(config: dict[str, Any], ssh_servers: list[dict[str, Any]]) -> list[FunctionTool]:
    """Return the function tools allowed by this agent's enabled capabilities."""
    tools = []
    grounded = documents_grounded(config)
    if config['code']['enabled'] or config['documents']['enabled'] and not grounded:
        tools.extend([list_files, read_file, search_files, shell_run])
    if config['documents']['enabled']:
        tools.extend([search_documents, read_document])
    if grounded:
        tools.append(report_not_covered)
    if config['code']['enabled']:
        tools.extend([code_search, ast_grep])
    if config['code']['enabled'] and config['code'].get('graphs'):
        tools.append(code_graph)
    if ssh_servers:
        tools.append(ssh_run)
    return tools
```

- [ ] **Step 6: Run tests**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_document_tools.py tests/test_sdk_agent.py tests/test_function_tools.py -q`
Expected: all pass. If an existing test asserts an exact tool list for a documents-enabled config, add `search_documents` and `read_document` to its expectation (they are now part of the Documents capability).

- [ ] **Step 7: Commit**

```bash
git add services/admin-api/src/knowledge_admin/grounding.py services/admin-api/src/knowledge_admin/tools/documents.py services/admin-api/src/knowledge_admin/agents.py services/admin-api/tests/test_document_tools.py services/admin-api/tests/test_sdk_agent.py
git commit -m "feat(agents): knowledge-base search tools with citations and grounding policy"
```

---

### Task 4: Shared turn service

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/agent_sessions.py`
- Test: `services/admin-api/tests/test_agent_runs.py` (append)

**Interfaces:**
- Consumes: `grounding.result` (Task 3), migration columns (Task 1).
- Produces:
  - `create_session(conn, *, session_id, project_id, actor_id, config, title, is_test, channel: str = 'admin', idle_timeout: timedelta | None = None) -> dict`
  - `load_session(store, conn, app_name: str, agent_id: str, session_id: UUID, *, owner: UUID | None = None) -> dict` (raises `HTTPException(404, 'Session not found for this agent.')`)
  - `async start_turn(app_state, store, app_name, agent_id, value: RunInput, actor: tuple[str, str], *, channel='admin', idle_timeout=None, owner_only=False, prepare=None, validate=None) -> AsyncIterator[tuple[str, Any]]`: it raises `HTTPException` before any turn starts and yields `('started', run)`, `('activity', item)`, `('heartbeat', None)` and `('completed', run)`. `prepare(config) -> config` runs for new sessions only; `validate(config)` runs on the config about to execute, and a `ValueError` from it becomes a 409.
  - The completed run's `context['grounding']` holds the `grounding.result` dict for grounded runs.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_agent_runs.py`)

```python
from datetime import timedelta
from fastapi import HTTPException
from knowledge_admin.agent_sessions import create_session, expire_sessions, load_session


def grounded_run(answer, state):
    async def run(config, task, **kwargs):
        return SimpleNamespace(final_output=answer,
            context_wrapper=SimpleNamespace(usage=Usage(), context={'grounding': state}),
            to_input_list=lambda: [{'role': 'user', 'content': task}, {'role': 'assistant', 'content': answer}])
    return run


def test_completed_run_carries_grounding_summary(direct, monkeypatch):
    state = {'excerpts': [{'n': 1, 'path': 'payments/retries.md', 'folder': 'payments', 'text': 'Three retries.'}],
             'searches': 1, 'empty_searches': 0, 'reported': None}
    monkeypatch.setattr('knowledge_admin.agent_sessions.agent', grounded_run('Three retries [1].', state))
    client, _, path = direct
    completed = events(client.post(path, headers=HEADERS, json={'task': 'How many retries?'}))[-1]
    assert completed['context']['grounding']['sources'] == [
        {'n': 1, 'path': 'payments/retries.md', 'heading_path': 'payments', 'anchor': None}]
    assert completed['context']['grounding']['not_covered'] is False


def test_ungrounded_run_has_no_grounding(direct):
    client, _, path = direct
    completed = events(client.post(path, headers=HEADERS, json={'task': 'Inspect'}))[-1]
    assert 'grounding' not in completed['context']


def test_idle_timeout_controls_expiry(direct, store):
    _, agent_id, _ = direct
    from knowledge_admin.agents import get_agent_config
    config = get_agent_config(agent_id, conninfo=store.conninfo)
    with store.connect() as conn:
        project, actor = store.project(conn, 'shop'), store.actor(conn, 'reader-1', 'reader@test')
        long = create_session(conn, session_id=uuid4(), project_id=project, actor_id=actor, config=config,
                              title='Long', is_test=False, channel='reader', idle_timeout=timedelta(days=7))
        short = create_session(conn, session_id=uuid4(), project_id=project, actor_id=actor, config=config,
                               title='Short', is_test=False)
        conn.execute("UPDATE ka.agent_sessions SET last_activity_at=now()-interval '2 hours'")
    expire_sessions(store)
    with store.connect() as conn:
        status = {row['id']: row['status'] for row in conn.execute('SELECT id,status FROM ka.agent_sessions')}
    assert status[long['id']] == 'active' and status[short['id']] == 'expired'
    assert long['channel'] == 'reader' and short['channel'] == 'admin'


def test_load_session_owner_filter(direct, store):
    _, agent_id, _ = direct
    from knowledge_admin.agents import get_agent_config
    config = get_agent_config(agent_id, conninfo=store.conninfo)
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        alice, bob = store.actor(conn, 'alice', 'a@test'), store.actor(conn, 'bob', 'b@test')
        saved = create_session(conn, session_id=uuid4(), project_id=project, actor_id=alice, config=config,
                               title='Mine', is_test=False, channel='reader')
        assert load_session(store, conn, 'shop', agent_id, saved['id'], owner=alice)['id'] == saved['id']
        with pytest.raises(HTTPException) as denied:
            load_session(store, conn, 'shop', agent_id, saved['id'], owner=bob)
        assert denied.value.status_code == 404
        conn.execute('UPDATE ka.agent_sessions SET hidden_by_owner_at=now() WHERE id=%s', (saved['id'],))
        with pytest.raises(HTTPException):
            load_session(store, conn, 'shop', agent_id, saved['id'], owner=alice)
        assert load_session(store, conn, 'shop', agent_id, saved['id'])['id'] == saved['id']
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_agent_runs.py -q`
Expected: FAIL — `ImportError: cannot import name 'load_session'`.

- [ ] **Step 3: Implement**

In `agent_sessions.py`:

1. Add imports: `from collections.abc import AsyncIterator, Callable`, `from datetime import timedelta`, `from knowledge_admin import grounding`.

2. Replace `create_session` with:

```python
def create_session(conn: psycopg.Connection, *, session_id: UUID, project_id: UUID,
                   actor_id: UUID, config: dict[str, Any], title: str, is_test: bool,
                   channel: str = 'admin', idle_timeout: timedelta | None = None) -> dict[str, Any]:
    """Create the same pinned agent session for a test, an admin live conversation, or a Reader conversation."""
    if not config['enabled']:
        raise ValueError('Agent is disabled.')
    if config['model'] is None or not config['model']['enabled']:
        raise ValueError('Select an enabled model in this agent’s General tab.')
    return conn.execute('''INSERT INTO ka.agent_sessions
        (id,project_id,agent,actor_id,title,config,agent_version_id,is_test,channel,idle_timeout)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,coalesce(%s::interval,interval '1 hour')) RETURNING *,
        last_activity_at+idle_timeout AS expires_at,config->'config_version' AS config_version''',
        (session_id,project_id,config['id'],actor_id,title,Jsonb(config),
         config.get('config_version',{}).get('id'),is_test,channel,idle_timeout)).fetchone()
```

3. In `expire_sessions`, change `last_activity_at <= now()-interval '1 hour'` to `last_activity_at <= now()-s.idle_timeout`.

4. Add module-level `load_session` and delete the closure `session(...)` inside `build_api`; replace every `session(conn, agent_id, session_id)` / `session(setup, agent_id, session_id)` call in `build_api` with `load_session(store, conn, app_name, agent_id, session_id)` (same argument positions):

```python
def load_session(store, conn, app_name: str, agent_id: str, session_id: UUID, *, owner: UUID | None = None) -> dict[str, Any]:
    """Load one session of this agent; with owner, only that principal's visible session."""
    value = conn.execute("""SELECT s.*,s.last_activity_at+s.idle_timeout AS expires_at
        FROM ka.agent_sessions s WHERE s.id=%s AND s.agent=%s AND s.project_id=%s
        AND (%s::uuid IS NULL OR (s.actor_id=%s AND s.hidden_by_owner_at IS NULL))""",
        (session_id, agent_id, store.project(conn, app_name), owner, owner)).fetchone()
    if value is None:
        raise HTTPException(404, 'Session not found for this agent.')
    return value
```

5. In the `GET /agents/{agent_id}/sessions` query, replace `last_activity_at+interval '1 hour' AS expires_at` with `last_activity_at+idle_timeout AS expires_at`.

6. Move the body of the `start` route into module-level `start_turn`, keeping every statement in the original order, with only these changes:
   - The signature and docstring:
     ```python
     async def start_turn(app_state, store, app_name: str, agent_id: str, value: RunInput, actor: tuple[str, str], *,
                          channel: str = 'admin', idle_timeout: timedelta | None = None, owner_only: bool = False,
                          prepare: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
                          validate: Callable[[dict[str, Any]], None] | None = None) -> AsyncIterator[tuple[str, Any]]:
         """Start one agent turn and return its events; raises HTTPException before the turn starts.

         Yields ('started', run), ('activity', item), ('heartbeat', None) and ('completed', run).
         A disconnecting consumer does not stop the turn.
         """
     ```
   - The first lines become:
     ```python
         await asyncio.to_thread(expire_sessions, store)
         owner = None
         try:
             if owner_only:
                 with store.connect() as owner_connection:
                     owner = store.actor(owner_connection, *actor)
             if value.session_id is not None:
                 with store.connect() as saved_connection:
                     saved = load_session(store, saved_connection, app_name, agent_id, value.session_id, owner=owner)
     ```
     (the existing status, version and test/live checks follow unchanged).
   - In the `else:` branch, after `config = await asyncio.to_thread(get_agent_config, ...)`, add:
     ```python
                 if prepare is not None:
                     config = prepare(config)
     ```
   - Immediately before `if not config['enabled']:`, add:
     ```python
             if validate is not None:
                 validate(config)
     ```
   - The new-session `create_session(...)` call gains `channel=channel, idle_timeout=idle_timeout`; the follow-up branch calls `load_session(store, setup, app_name, agent_id, session_id, owner=owner)`.
   - Inside `execute()`, directly after `context['usage'] = jsonable_encoder(result.context_wrapper.usage)`, add:
     ```python
                    run_context = getattr(result.context_wrapper, 'context', None)
                    summary = grounding.result(answer, run_context.get('grounding') if isinstance(run_context, dict) else None)
                    if summary is not None:
                        context['grounding'] = json.loads(redact(json.dumps(summary), credentials.values()))
     ```
   - `request.app.state.agent_runs` becomes `app_state.agent_runs`.
   - Replace the inner `stream()` with a tuple stream and return it:
     ```python
         async def stream():
             yield 'started', jsonable_encoder(row)
             while True:
                 try:
                     event, data = await asyncio.wait_for(events.get(), timeout=1)
                 except TimeoutError:
                     if execution.done():
                         return
                     yield 'heartbeat', None
                     continue
                 yield event, data
                 if event == 'completed':
                     return
         return stream()
     ```

7. The route becomes:

```python
    @router.post('/agents/{agent_id}/runs')
    async def start(agent_id: str, value: RunInput, request: Request):
        database()
        turn = await start_turn(request.app.state, store, app_name, agent_id, value, request_actor(request))
        async def stream():
            # Disconnecting only closes this subscriber. The session/turn continue.
            async for event, data in turn:
                yield ': heartbeat\n\n' if event == 'heartbeat' else 'event: '+event+'\ndata: '+json.dumps(data)+'\n\n'
        return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-store','X-Accel-Buffering':'no'})
```

- [ ] **Step 4: Run tests**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_agent_runs.py tests/test_agent_sessions.py tests/test_agent_versions.py tests/test_session_migration.py -q`
Expected: all pass (existing admin behavior unchanged).

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/agent_sessions.py services/admin-api/tests/test_agent_runs.py
git commit -m "refactor(agents): shared turn service with idle timeout, owner checks and grounding summary"
```

---

### Task 5: Knowledge-tier policy and Reader assistant settings API

**Files:**
- Create: `services/admin-api/src/knowledge_admin/reader_policy.py`
- Create: `services/admin-api/src/knowledge_admin/reader_settings.py`
- Modify: `services/admin-api/src/knowledge_admin/api.py` (include router)
- Modify: `services/admin-api/src/knowledge_admin/profiles_api.py` (`delete`)
- Test: `services/admin-api/tests/test_reader_settings.py`

**Interfaces:**
- Consumes: `get_agent_config`, `config['documents']['grounded']`.
- Produces:
  - `reader_policy.knowledge_violations(config) -> list[str]`
  - `reader_policy.NOT_CONFIGURED`, `reader_policy.NOT_ALLOWED` (the exact strings from Global Constraints)
  - `GET /api/reader/settings` → `{knowledge_agent_id: str | None, revision: int, updated_at, candidates: [{id, name, enabled, violations: [str]}]}`
  - `PUT /api/reader/settings` with `{knowledge_agent_id, revision}` → the same shape

- [ ] **Step 1: Write the failing tests**

```python
"""The Reader assistant is chosen in admin and must satisfy the Knowledge tier."""
import pytest

from knowledge_admin.reader_policy import knowledge_violations
from test_api import _client
from test_profiles import create
from test_repositories import HEADERS


def base(**changes):
    config = {'documents': {'enabled': True, 'grounded': True}, 'ssh': {'enabled': False},
              'mcp': {'enabled': False}, 'code': {'enabled': False}}
    for key, value in changes.items():
        config[key] = value
    return config


@pytest.mark.parametrize('changes, reason', [
    ({'ssh': {'enabled': True}}, 'uses SSH'),
    ({'mcp': {'enabled': True}}, 'uses MCP tools'),
    ({'code': {'enabled': True}}, 'uses code projects'),
    ({'documents': {'enabled': False}}, 'document search is off'),
    ({'documents': {'enabled': True}}, 'Answer only from documents is off'),
])
def test_knowledge_violations(changes, reason):
    assert knowledge_violations(base()) == []
    assert reason in knowledge_violations(base(**changes))


@pytest.fixture
def admin(store, tmp_path, monkeypatch):
    monkeypatch.setenv('CODE_ROOT', str(tmp_path.resolve()))
    monkeypatch.setenv('RAG_SOURCE_ROOT', str(tmp_path.resolve()))
    return _client(tmp_path, repository_store=store)


def test_settings_default_to_search_and_list_candidates(admin):
    settings = admin.get('/api/reader/settings', headers=HEADERS).json()
    assert settings['knowledge_agent_id'] == 'search' and settings['revision'] == 1
    by_id = {row['id']: row for row in settings['candidates']}
    assert 'coordinator' not in by_id
    assert 'uses SSH' in by_id['operations']['violations']


def test_save_rejects_violating_agent_and_stale_revision(admin, store):
    custom = create(admin, 'Docs helper')
    rejected = admin.put('/api/reader/settings', headers=HEADERS, json={'knowledge_agent_id': custom['id'], 'revision': 1})
    assert rejected.status_code == 400 and 'document search is off' in rejected.text
    with store.connect() as conn:
        conn.execute('UPDATE ka.agents SET documents_enabled=true,documents_grounded=true WHERE id=%s', (custom['id'],))
    saved = admin.put('/api/reader/settings', headers=HEADERS, json={'knowledge_agent_id': custom['id'], 'revision': 1})
    assert saved.status_code == 200 and saved.json()['knowledge_agent_id'] == custom['id'] and saved.json()['revision'] == 2
    stale = admin.put('/api/reader/settings', headers=HEADERS, json={'knowledge_agent_id': 'search', 'revision': 1})
    assert stale.status_code == 409
    missing = admin.put('/api/reader/settings', headers=HEADERS, json={'knowledge_agent_id': 'nope', 'revision': 2})
    assert missing.status_code == 400
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM ka.audit_events WHERE action='reader.settings.updated'").fetchone()['n'] == 1


def test_reader_agent_cannot_be_deleted(admin, store):
    custom = create(admin, 'Docs helper')
    with store.connect() as conn:
        conn.execute('UPDATE ka.agents SET documents_enabled=true,documents_grounded=true WHERE id=%s', (custom['id'],))
    admin.put('/api/reader/settings', headers=HEADERS, json={'knowledge_agent_id': custom['id'], 'revision': 1})
    revision = admin.get(f"/api/agents/{custom['id']}", headers=HEADERS).json()['revision']
    response = admin.request('DELETE', f"/api/agents/{custom['id']}", headers=HEADERS,
                             json={'revision': revision, 'confirmed': True})
    assert response.status_code == 409
    assert response.json()['error']['message'] == 'This agent is the Reader assistant. Choose another Reader agent first.'


def test_settings_require_admin(admin):
    assert admin.get('/api/reader/settings').status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_reader_settings.py -q`
Expected: FAIL — `ModuleNotFoundError: knowledge_admin.reader_policy`.

- [ ] **Step 3: Implement `reader_policy.py`**

```python
"""Which agent configurations may answer Reader users (Knowledge tier)."""
from typing import Any

NOT_CONFIGURED = "The assistant isn't configured yet. Ask an administrator."
NOT_ALLOWED = "The assistant's configuration isn't allowed in the Reader."


def knowledge_violations(config: dict[str, Any]) -> list[str]:
    """Readable reasons this configuration cannot serve every signed-in Reader user; empty when allowed."""
    reasons = []
    if config['ssh']['enabled']:
        reasons.append('uses SSH')
    if config['mcp']['enabled']:
        reasons.append('uses MCP tools')
    if config['code']['enabled']:
        reasons.append('uses code projects')
    if not config['documents']['enabled']:
        reasons.append('document search is off')
    elif not config['documents'].get('grounded', False):
        reasons.append('Answer only from documents is off')
    return reasons
```

- [ ] **Step 4: Implement `reader_settings.py`**

```python
"""Administrator choice of the agent that answers Reader users."""
from fastapi import APIRouter, HTTPException, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from knowledge_admin.access import request_actor
from knowledge_admin.agents import get_agent_config
from knowledge_admin.reader_policy import knowledge_violations


class SettingsInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    knowledge_agent_id: str = Field(min_length=1, max_length=100)
    revision: int = Field(ge=1, strict=True)


def violations(agent_id: str, conninfo: str) -> list[str]:
    try:
        return knowledge_violations(get_agent_config(agent_id, conninfo=conninfo))
    except LookupError:
        raise
    except ValueError as exc:
        return [str(exc)]


def build_api(app_name: str, store) -> APIRouter:
    router = APIRouter(prefix='/reader/settings')

    def database():
        if store is None:
            raise HTTPException(503, 'Agent storage is not configured.')
        return store

    def read() -> dict:
        with database().connect() as conn:
            row = conn.execute('SELECT knowledge_agent_id,revision,updated_at FROM ka.reader_settings').fetchone()
            agents = conn.execute('SELECT id,name,enabled FROM ka.agents WHERE NOT is_coordinator ORDER BY lower(name),id').fetchall()
        candidates = [{**agent, 'violations': violations(agent['id'], store.conninfo)} for agent in agents]
        settings = row or {'knowledge_agent_id': None, 'revision': 0, 'updated_at': None}
        return {**settings, 'candidates': candidates}

    @router.get('')
    def get_settings():
        return read()

    @router.put('')
    def save(value: SettingsInput, request: Request):
        try:
            reasons = violations(value.knowledge_agent_id, database().conninfo)
        except LookupError:
            raise HTTPException(400, 'Choose an existing agent.') from None
        if reasons:
            raise HTTPException(400, 'This agent cannot answer Reader users: ' + '; '.join(reasons) + '.')
        with store.connect() as conn:
            actor = store.actor(conn, *request_actor(request))
            current = conn.execute('SELECT revision FROM ka.reader_settings FOR UPDATE').fetchone()
            if (current['revision'] if current else 0) != value.revision and current is not None:
                raise HTTPException(409, 'Reader settings changed. Reload before saving.')
            conn.execute('''INSERT INTO ka.reader_settings(knowledge_agent_id,updated_by) VALUES (%s,%s)
                ON CONFLICT (singleton) DO UPDATE SET knowledge_agent_id=EXCLUDED.knowledge_agent_id,
                revision=ka.reader_settings.revision+1,updated_by=EXCLUDED.updated_by,updated_at=now()''',
                (value.knowledge_agent_id, actor))
            conn.execute('''INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,details)
                VALUES (%s,%s,'reader.settings.updated','reader_settings',%s)''',
                (store.project(conn, app_name), actor, Jsonb({'knowledge_agent_id': value.knowledge_agent_id})))
        return read()

    return router
```

- [ ] **Step 5: Register and protect delete**

In `api.py`, add `reader_settings` to the `from knowledge_admin import ...` line and `router.include_router(reader_settings.build_api(app_name, repository_store))` after the profiles router.

In `profiles_api.py` `delete`, right after the coordinator check, add:

```python
            if conn.execute('SELECT 1 FROM ka.reader_settings WHERE knowledge_agent_id=%s', (agent_id,)).fetchone():
                raise HTTPException(409, 'This agent is the Reader assistant. Choose another Reader agent first.')
```

- [ ] **Step 6: Run tests**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_reader_settings.py tests/test_profiles.py -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add services/admin-api/src/knowledge_admin/reader_policy.py services/admin-api/src/knowledge_admin/reader_settings.py services/admin-api/src/knowledge_admin/api.py services/admin-api/src/knowledge_admin/profiles_api.py services/admin-api/tests/test_reader_settings.py
git commit -m "feat(admin): Reader assistant setting with knowledge-tier policy"
```

---

### Task 6: Internal Reader endpoints and service key

**Files:**
- Create: `services/admin-api/src/knowledge_admin/reader_api.py`
- Modify: `services/admin-api/src/knowledge_admin/app.py` (mount router)
- Modify: `scripts/setup.py` (generate `READER_AGENT_KEY`), `.env.example`, `deploy/compose.yaml` (admin-api env)
- Test: `services/admin-api/tests/test_reader_api.py`, `tests/test_setup.py` (append)

**Interfaces:**
- Consumes: `start_turn`, `load_session`, `RunInput` (Task 4); `knowledge_violations`, `NOT_CONFIGURED`, `NOT_ALLOWED` (Task 5).
- Produces (all require `X-Reader-Key` and `X-Reader-User`; optional `X-Reader-Email`):
  - `POST /internal/reader/runs` with body `{id?, session_id?, question, folders: list[str] | null, include_subfolders}` → SSE events:
    - `session {id}` (new sessions only)
    - `activity {tool, argument}` (tool starts only)
    - `completed {status, answer, error, grounding}`
  - `GET /internal/reader/sessions` → `{sessions: [{id, title, status, created_at, last_activity_at}]}`
  - `GET /internal/reader/sessions/{id}` → `{id, title, status, folders, include_subfolders, messages: [{role, content, sources?, not_covered?}]}`
  - `DELETE /internal/reader/sessions/{id}` → 204
  - `narrow(config, folders, include_subfolders) -> config`; `READER_IDLE_TIMEOUT = timedelta(days=7)`; `ENDED`, `BUSY` strings.

- [ ] **Step 1: Write the failing tests**

```python
"""Reader conversations run through the shared turn service on the internal network only."""
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from agents.usage import Usage

from knowledge_admin.reader_api import narrow
from test_api import _client
from test_sdk_agent import responses_api
from test_repositories import HEADERS

KEY = 'reader-test-key'


def reader_headers(user='alice'):
    return {'X-Reader-Key': KEY, 'X-Reader-User': user, 'X-Reader-Email': f'{user}@example.test'}


def sse(response):
    events, name = [], None
    for line in response.text.splitlines():
        if line.startswith('event: '):
            name = line[7:]
        elif line.startswith('data: '):
            events.append((name, json.loads(line[6:])))
    return events


@pytest.fixture
def reader(store, tmp_path, monkeypatch, responses_api):
    monkeypatch.setenv('CODE_ROOT', str(tmp_path.resolve()))
    monkeypatch.setenv('RAG_SOURCE_ROOT', str(tmp_path.resolve()))
    monkeypatch.setenv('READER_AGENT_KEY', KEY)
    client = _client(tmp_path, repository_store=store)
    model = client.post('/api/llm/models', headers=HEADERS, json={
        'name': 'Reader model', 'provider': 'openai', 'api_format': 'responses', 'model': 'reader-fixture',
        'base_url': responses_api[0], 'api_key': 'reader-model-secret'}).json()
    with store.connect() as conn:
        conn.execute('''UPDATE ka.agents SET model_id=%s,enabled=true,documents_enabled=true,documents_grounded=true,
            all_folders=true,repositories_enabled=false,ssh_enabled=false,mcp_enabled=false WHERE id='search' ''', (model['id'],))
    seen = []
    async def fake(config, task, **kwargs):
        seen.append(config)
        state = {'excerpts': [{'n': 1, 'path': 'payments/retries.md', 'folder': 'payments', 'text': 'Three.'}],
                 'searches': 1, 'empty_searches': 0, 'reported': None}
        return SimpleNamespace(final_output='Three retries [1].',
            context_wrapper=SimpleNamespace(usage=Usage(), context={'grounding': state}),
            to_input_list=lambda: [*kwargs.get('history', []), {'role': 'user', 'content': task},
                                   {'role': 'assistant', 'content': 'Three retries [1].'}])
    monkeypatch.setattr('knowledge_admin.agent_sessions.agent', fake)
    return client, seen


def ask(client, user='alice', **body):
    return client.post('/internal/reader/runs', headers=reader_headers(user), json={'question': 'How many retries?', **body})


def test_key_is_required_and_routes_are_not_under_api(reader, monkeypatch):
    client, _ = reader
    assert client.get('/internal/reader/sessions', headers={**reader_headers(), 'X-Reader-Key': 'wrong'}).status_code == 401
    assert client.get('/internal/reader/sessions', headers={'X-Reader-User': 'alice'}).status_code == 401
    assert client.get('/internal/reader/sessions', headers={'X-Reader-Key': KEY}).status_code == 403
    assert client.get('/api/internal/reader/sessions', headers={**HEADERS, **reader_headers()}).status_code == 404
    monkeypatch.delenv('READER_AGENT_KEY')
    assert client.get('/internal/reader/sessions', headers=reader_headers()).status_code == 503


def test_new_conversation_streams_session_activity_and_cited_answer(reader, store):
    client, _ = reader
    response = ask(client)
    assert response.status_code == 200, response.text
    events = sse(response)
    assert events[0][0] == 'session'
    completed = events[-1]
    assert completed[0] == 'completed' and completed[1]['status'] == 'completed'
    assert completed[1]['grounding']['sources'][0]['path'] == 'payments/retries.md'
    session_id = events[0][1]['id']
    with store.connect() as conn:
        row = conn.execute('''SELECT s.channel,s.is_test,s.idle_timeout,p.email FROM ka.agent_sessions s
            JOIN ka.principals p ON p.id=s.actor_id WHERE s.id=%s''', (session_id,)).fetchone()
    assert row['channel'] == 'reader' and row['is_test'] is False and row['idle_timeout'].days == 7
    assert row['email'] == 'alice@example.test'
    assert 'reader-model-secret' not in response.text


def test_follow_up_list_transcript_and_owner_isolation(reader):
    client, _ = reader
    session_id = sse(ask(client))[0][1]['id']
    follow = sse(ask(client, session_id=session_id, question='And after that?'))
    assert follow[0][0] != 'session' and follow[-1][1]['status'] == 'completed'
    sessions = client.get('/internal/reader/sessions', headers=reader_headers()).json()['sessions']
    assert [row['id'] for row in sessions] == [session_id]
    transcript = client.get(f'/internal/reader/sessions/{session_id}', headers=reader_headers()).json()
    assert [m['role'] for m in transcript['messages']] == ['user', 'assistant', 'user', 'assistant']
    assert transcript['messages'][1]['sources'][0]['n'] == 1 and transcript['messages'][1]['not_covered'] is False
    assert client.get('/internal/reader/sessions', headers=reader_headers('bob')).json()['sessions'] == []
    assert client.get(f'/internal/reader/sessions/{session_id}', headers=reader_headers('bob')).status_code == 404
    assert ask(client, user='bob', session_id=session_id).status_code == 404
    assert client.delete(f'/internal/reader/sessions/{session_id}', headers=reader_headers('bob')).status_code == 404


def test_delete_hides_from_owner_but_admin_keeps_transcript(reader):
    client, _ = reader
    session_id = sse(ask(client))[0][1]['id']
    assert client.delete(f'/internal/reader/sessions/{session_id}', headers=reader_headers()).status_code == 204
    assert client.get('/internal/reader/sessions', headers=reader_headers()).json()['sessions'] == []
    assert client.get(f'/api/agents/search/sessions/{session_id}', headers=HEADERS).status_code == 200


def test_tier_and_configuration_errors(reader, store):
    client, _ = reader
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET ssh_enabled=true WHERE id='search'")
    response = ask(client)
    assert response.status_code == 409
    assert response.json()['error']['message'] == "The assistant's configuration isn't allowed in the Reader."
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET ssh_enabled=false,model_id=NULL WHERE id='search'")
    assert ask(client).json()['error']['message'] == "The assistant isn't configured yet. Ask an administrator."


def test_ended_conversation(reader, store):
    client, _ = reader
    session_id = sse(ask(client))[0][1]['id']
    with store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions SET status='expired' WHERE id=%s", (session_id,))
    response = ask(client, session_id=session_id)
    assert response.status_code == 409
    assert response.json()['error']['message'] == 'This conversation has ended. Start a new one.'


def test_switching_reader_agent_keeps_existing_conversations(reader, store):
    client, seen = reader
    session_id = sse(ask(client))[0][1]['id']
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET documents_grounded=true,documents_enabled=true,model_id=(SELECT model_id FROM ka.agents WHERE id='search') WHERE id='code'")
        conn.execute("UPDATE ka.agents SET repositories_enabled=false WHERE id='code'")
        conn.execute("UPDATE ka.reader_settings SET knowledge_agent_id='code'")
    follow = sse(ask(client, session_id=session_id, question='Again?'))
    assert follow[-1][1]['status'] == 'completed' and seen[-1]['id'] == 'search'
    assert [row['id'] for row in client.get('/internal/reader/sessions', headers=reader_headers()).json()['sessions']] == [session_id]


def test_narrow_keeps_only_intersection():
    config = {'documents': {'all_folders': True, 'folders': [
        {'search_folder': 'payments'}, {'search_folder': 'payments.runbooks'}, {'search_folder': 'people'}]}}
    assert narrow(config, None, True) is config
    narrowed = narrow(config, ['payments'], True)
    assert narrowed['documents']['all_folders'] is False
    assert [f['search_folder'] for f in narrowed['documents']['folders']] == ['payments', 'payments.runbooks']
    assert [f['search_folder'] for f in narrow(config, ['payments'], False)['documents']['folders']] == ['payments']
    assert narrow(config, ['finance'], True)['documents']['folders'] == []
```

Add this method to `SetupTests` in the repository-root `tests/test_setup.py`:

```python
    def test_setup_generates_reader_agent_key(self) -> None:
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copy(source / ".env.example", root / ".env.example")
            shutil.copytree(source / "examples", root / "examples")
            initialize(root)
            values = dict(re.findall(r"^([A-Z][A-Z0-9_]*)=(.*)$", (root / ".env").read_text(encoding="utf-8"), re.MULTILINE))
            self.assertGreaterEqual(len(values["READER_AGENT_KEY"]), 32)
            self.assertNotEqual(values["READER_AGENT_KEY"], values["RAG_BACKOFFICE_KEY"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_reader_api.py -q`
Expected: FAIL — `ModuleNotFoundError: knowledge_admin.reader_api`.

- [ ] **Step 3: Implement `reader_api.py`**

```python
"""Reader conversations, reachable only from docs-wiki on the internal network."""
import asyncio
import hmac
import json
import os
from datetime import timedelta
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from knowledge_admin.agent_sessions import RunInput, expire_sessions, start_turn
from knowledge_admin.reader_policy import NOT_ALLOWED, NOT_CONFIGURED, knowledge_violations

READER_IDLE_TIMEOUT = timedelta(days=7)
ENDED = 'This conversation has ended. Start a new one.'
BUSY = 'Wait for the current answer.'
UNKNOWN = 'Unknown conversation'
FRIENDLY = {'This session already has a running message or tuning review.': BUSY,
            'This session is closed or expired. Start a new session.': ENDED}
FolderPath = Annotated[str, Field(pattern=r'^[a-zA-Z0-9_-]+(/[a-zA-Z0-9_-]+)*$', max_length=500)]


class ReaderRunInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    id: UUID = Field(default_factory=uuid4)
    session_id: UUID | None = None
    question: str = Field(min_length=1, max_length=10000)
    folders: list[FolderPath] | None = Field(default=None, max_length=200)
    include_subfolders: StrictBool = True


def narrow(config: dict[str, Any], folders: list[str] | None, include_subfolders: bool) -> dict[str, Any]:
    """Limit the agent's document folders to the user's selection; never broaden them."""
    if folders is None:
        return config
    chosen = set(folders)
    def selected(path: str) -> bool:
        return path in chosen or include_subfolders and any(path.startswith(folder + '/') for folder in chosen)
    documents = {**config['documents'], 'all_folders': False, 'folders': [
        folder for folder in config['documents']['folders'] if selected(folder['search_folder'].replace('.', '/'))]}
    return {**config, 'documents': documents}


def validate(config: dict[str, Any]) -> None:
    if not config['enabled'] or config['model'] is None or not config['model']['enabled']:
        raise ValueError(NOT_CONFIGURED)
    if knowledge_violations(config):
        raise ValueError(NOT_ALLOWED)


def build_api(app_name: str, store) -> APIRouter:
    router = APIRouter(prefix='/internal/reader')

    def reader(request: Request) -> tuple[str, str]:
        expected = os.environ.get('READER_AGENT_KEY')
        if store is None or not expected:
            raise HTTPException(503, 'Reader chat is not configured.')
        if not hmac.compare_digest(request.headers.get('x-reader-key', '').encode(), expected.encode()):
            raise HTTPException(401, 'Invalid reader key.')
        subject = request.headers.get('x-reader-user', '')
        if not subject:
            raise HTTPException(403, 'Authenticated user identity required')
        return subject, request.headers.get('x-reader-email', '')

    def owned(conn, actor: tuple[str, str], session_id: UUID) -> dict[str, Any]:
        row = conn.execute('''SELECT s.* FROM ka.agent_sessions s WHERE s.id=%s AND s.project_id=%s AND s.channel='reader'
            AND s.actor_id=%s AND s.hidden_by_owner_at IS NULL''',
            (session_id, store.project(conn, app_name), store.actor(conn, *actor))).fetchone()
        if row is None:
            raise HTTPException(404, UNKNOWN)
        return row

    @router.post('/runs')
    async def run(value: ReaderRunInput, request: Request):
        actor = reader(request)
        await asyncio.to_thread(expire_sessions, store)
        if value.session_id is not None:
            with store.connect() as conn:
                saved = owned(conn, actor, value.session_id)
            if saved['status'] != 'active':
                raise HTTPException(409, ENDED)
            agent_id = saved['agent']
        else:
            with store.connect() as conn:
                settings = conn.execute('SELECT knowledge_agent_id FROM ka.reader_settings').fetchone()
            if settings is None:
                raise HTTPException(409, NOT_CONFIGURED)
            agent_id = settings['knowledge_agent_id']
        turn_input = RunInput(id=value.id, session_id=value.session_id, is_test=False, task=value.question)
        try:
            turn = await start_turn(request.app.state, store, app_name, agent_id, turn_input, actor,
                channel='reader', idle_timeout=READER_IDLE_TIMEOUT, owner_only=True,
                prepare=lambda config: narrow(config, value.folders, value.include_subfolders), validate=validate)
        except HTTPException as exc:
            raise HTTPException(exc.status_code, FRIENDLY.get(exc.detail, exc.detail)) from None

        async def stream():
            async for event, data in turn:
                if event == 'heartbeat':
                    yield ': heartbeat\n\n'
                    continue
                if event == 'started':
                    if value.session_id is None:
                        payload = ('session', {'id': str(data['session_id'])})
                    else:
                        continue
                elif event == 'activity':
                    if data.get('kind') != 'tool' or data.get('status') != 'inProgress':
                        continue
                    payload = ('activity', {'tool': data['title'], 'argument': data.get('detail', '')})
                else:
                    payload = ('completed', {'status': data['status'], 'answer': data['answer'], 'error': data['error'],
                                             'grounding': data['context'].get('grounding')})
                yield 'event: ' + payload[0] + '\ndata: ' + json.dumps(payload[1]) + '\n\n'
        return StreamingResponse(stream(), media_type='text/event-stream',
                                 headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})

    @router.get('/sessions')
    def sessions(request: Request):
        actor = reader(request)
        with store.connect() as conn:
            rows = conn.execute('''SELECT id,title,status,created_at,last_activity_at FROM ka.agent_sessions
                WHERE project_id=%s AND channel='reader' AND actor_id=%s AND hidden_by_owner_at IS NULL
                ORDER BY last_activity_at DESC LIMIT 50''', (store.project(conn, app_name), store.actor(conn, *actor))).fetchall()
        return {'sessions': rows}

    @router.get('/sessions/{session_id}')
    def transcript(session_id: UUID, request: Request):
        actor = reader(request)
        with store.connect() as conn:
            saved = owned(conn, actor, session_id)
            runs = conn.execute('''SELECT task,answer,status,context->'grounding' AS grounding FROM ka.operations_runs
                WHERE session_id=%s ORDER BY created_at,id''', (session_id,)).fetchall()
        messages = []
        for run in runs:
            messages.append({'role': 'user', 'content': run['task']})
            if run['status'] == 'completed':
                grounding = run['grounding'] or {}
                messages.append({'role': 'assistant', 'content': run['answer'], 'sources': grounding.get('sources', []),
                                 'not_covered': bool(grounding.get('not_covered'))})
        documents = saved['config']['documents']
        folders = None if documents.get('all_folders') else [f['search_folder'].replace('.', '/') for f in documents['folders']]
        return {'id': saved['id'], 'title': saved['title'], 'status': saved['status'], 'folders': folders,
                'include_subfolders': documents.get('include_subfolders', True), 'messages': messages}

    @router.delete('/sessions/{session_id}', status_code=204)
    def delete(session_id: UUID, request: Request):
        actor = reader(request)
        with store.connect() as conn:
            owned(conn, actor, session_id)
            conn.execute('''UPDATE ka.agent_sessions SET status=CASE WHEN status='active' THEN 'closed' ELSE status END,
                closed_at=coalesce(closed_at,now()),hidden_by_owner_at=now() WHERE id=%s''', (session_id,))
            conn.execute("UPDATE ka.operations_runs SET status='cancelled',finished_at=now(),progress='Session closed.' WHERE session_id=%s AND status='running'", (session_id,))
        return Response(status_code=204)

    return router
```

- [ ] **Step 4: Mount the router outside `/api`**

In `app.py`, after `app.include_router(build_api(...))`:

```python
    from knowledge_admin import reader_api
    app.include_router(reader_api.build_api(cfg.apps[0].name, repository_store))
```

(`build_api` already rejects configurations without exactly one app, so `cfg.apps[0]` exists.)

- [ ] **Step 5: Key generation and compose**

- `scripts/setup.py`: add `"READER_AGENT_KEY"` to the generated-secrets tuple after `"RAG_BACKOFFICE_KEY"`.
- `.env.example`: add after `RAG_BACKOFFICE_KEY=`:
  ```
  # Internal key for Reader chat calls from docs-wiki to admin-api (generated by make setup)
  READER_AGENT_KEY=
  ```
- `deploy/compose.yaml`, `admin-api.environment`: add `READER_AGENT_KEY: ${READER_AGENT_KEY:?Run make setup}`.

- [ ] **Step 6: Run tests**

Run:
```
cd services/admin-api && uv run --frozen pytest tests/test_reader_api.py tests/test_agent_runs.py -q
cd ../.. && python3 -m unittest discover -s tests -v
make compose-validate
```
Expected: all pass; compose config valid.

- [ ] **Step 7: Commit**

```bash
git add services/admin-api/src/knowledge_admin/reader_api.py services/admin-api/src/knowledge_admin/app.py services/admin-api/tests/test_reader_api.py scripts/setup.py .env.example deploy/compose.yaml tests/test_setup.py
git commit -m "feat(admin): internal Reader conversation endpoints"
```

---

### Task 7: Activity reads Reader conversations from PostgreSQL

**Files:**
- Create: `services/admin-api/src/knowledge_admin/activity.py`
- Modify: `services/admin-api/src/knowledge_admin/api.py` (remove the four `/admin/audit*` routes and the `docs_wiki.audit` import; include the new router)
- Test: `services/admin-api/tests/test_activity.py`

**Interfaces:**
- Consumes: reader sessions and runs (Task 6).
- Produces (paths unchanged for the UI):
  - `GET /api/admin/audit/users` → `{users: [{user_email, sessions, questions, last_ts}]}`
  - `GET /api/admin/audit/users/{email}/sessions` → `{sessions: [{session_key, first_question, questions, first_ts, last_ts}]}`
  - `GET /api/admin/audit/session/{key}` → `{rows: [{id: str, ts, user_email, session_key, agent_id, question, answer, status, sources_used, sources, not_covered, escalated: false}]}`

- [ ] **Step 1: Write the failing test**

```python
"""Activity shows Reader conversations stored as live agent sessions."""
from uuid import uuid4

from psycopg.types.json import Jsonb

from test_api import _client
from test_repositories import HEADERS


def test_activity_lists_reader_sessions_only(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        alice = store.actor(conn, 'alice', 'alice@example.test')
        reader_session, admin_session = uuid4(), uuid4()
        for session_id, channel in ((reader_session, 'reader'), (admin_session, 'admin')):
            conn.execute('''INSERT INTO ka.agent_sessions(id,project_id,agent,actor_id,title,config,is_test,channel)
                VALUES (%s,%s,'search',%s,'How many retries?','{}',false,%s)''', (session_id, project, alice, channel))
            conn.execute('''INSERT INTO ka.operations_runs(project_id,actor_id,agent,session_id,task,context,status,answer)
                VALUES (%s,%s,'search',%s,'How many retries?',%s,'completed','Three [1].')''',
                (project, alice, session_id, Jsonb({'grounding': {'sources': [{'n': 1, 'path': 'p/r.md', 'heading_path': 'p', 'anchor': None}], 'not_covered': False}})))
    users = client.get('/api/admin/audit/users', headers=HEADERS).json()['users']
    assert users == [{'user_email': 'alice@example.test', 'sessions': 1, 'questions': 1, 'last_ts': users[0]['last_ts']}]
    sessions = client.get('/api/admin/audit/users/alice@example.test/sessions', headers=HEADERS).json()['sessions']
    assert [row['session_key'] for row in sessions] == [str(reader_session)]
    rows = client.get(f'/api/admin/audit/session/{reader_session}', headers=HEADERS).json()['rows']
    assert rows[0]['answer'] == 'Three [1].' and rows[0]['sources'][0]['path'] == 'p/r.md'
    assert rows[0]['sources_used'] == ['docs'] and rows[0]['agent_id'] == 'search' and rows[0]['not_covered'] is False
    assert client.get('/api/admin/audit/session/not-a-uuid', headers=HEADERS).status_code == 404
    assert client.get(f'/api/admin/audit/session/{admin_session}', headers=HEADERS).json()['rows'] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_activity.py -q`
Expected: FAIL — the users list is empty (the old routes read `audit.db`).

- [ ] **Step 3: Implement `activity.py`**

```python
"""Administrator view of Reader conversations stored as live agent sessions."""
from uuid import UUID

from fastapi import APIRouter, HTTPException


def build_api(app_name: str, store) -> APIRouter:
    router = APIRouter(prefix='/admin/audit')

    def database():
        if store is None:
            raise HTTPException(503, 'Agent storage is not configured.')
        return store

    @router.get('/users')
    def users():
        with database().connect() as conn:
            rows = conn.execute('''SELECT p.email AS user_email,count(DISTINCT s.id) AS sessions,count(r.id) AS questions,
                max(coalesce(r.created_at,s.created_at)) AS last_ts FROM ka.agent_sessions s
                JOIN ka.principals p ON p.id=s.actor_id LEFT JOIN ka.operations_runs r ON r.session_id=s.id
                WHERE s.project_id=%s AND s.channel='reader' GROUP BY p.email ORDER BY last_ts DESC''',
                (store.project(conn, app_name),)).fetchall()
        return {'users': rows}

    @router.get('/users/{email}/sessions')
    def sessions(email: str):
        with database().connect() as conn:
            rows = conn.execute('''SELECT s.id::text AS session_key,s.title AS first_question,count(r.id) AS questions,
                min(coalesce(r.created_at,s.created_at)) AS first_ts,max(coalesce(r.created_at,s.created_at)) AS last_ts
                FROM ka.agent_sessions s JOIN ka.principals p ON p.id=s.actor_id
                LEFT JOIN ka.operations_runs r ON r.session_id=s.id
                WHERE s.project_id=%s AND s.channel='reader' AND p.email=%s
                GROUP BY s.id ORDER BY last_ts DESC''', (store.project(conn, app_name), email)).fetchall()
        return {'sessions': rows}

    @router.get('/session/{key}')
    def session(key: str):
        try:
            session_id = UUID(key)
        except ValueError:
            raise HTTPException(404, 'Unknown session') from None
        with database().connect() as conn:
            rows = conn.execute('''SELECT r.id::text AS id,r.created_at AS ts,p.email AS user_email,s.id::text AS session_key,
                s.agent AS agent_id,r.task AS question,r.answer,r.status,r.context->'grounding' AS grounding
                FROM ka.operations_runs r JOIN ka.agent_sessions s ON s.id=r.session_id
                JOIN ka.principals p ON p.id=r.actor_id
                WHERE s.id=%s AND s.project_id=%s AND s.channel='reader' ORDER BY r.created_at,r.id''',
                (session_id, store.project(conn, app_name))).fetchall()
        for row in rows:
            grounding = row.pop('grounding') or {}
            row['sources'] = grounding.get('sources', [])
            row['sources_used'] = ['docs'] if row['sources'] else []
            row['not_covered'] = bool(grounding.get('not_covered'))
            row['escalated'] = False
        return {'rows': rows}

    return router
```

In `api.py`: delete `from docs_wiki import audit` and the four `@router.get("/admin/audit...")` functions, add `activity` to the `from knowledge_admin import ...` line and `router.include_router(activity.build_api(app_name, repository_store))`. Leave the `state_root` import if other code in the file still uses it; otherwise remove it.

- [ ] **Step 4: Run tests**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_activity.py tests/test_api.py -q`
Expected: all pass. Delete any `test_api.py` tests that asserted the old `audit.db`-backed rows; they are replaced by `test_activity.py`.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/activity.py services/admin-api/src/knowledge_admin/api.py services/admin-api/tests/test_activity.py services/admin-api/tests/test_api.py
git commit -m "feat(admin): Activity shows Reader conversations from agent sessions"
```

---

### Task 8: Admin UI — grounding switch, Reader assistant card, sources in Test and Activity

**Files:**
- Modify: `services/admin-ui/app/lib/profiles.ts` (type and save payload)
- Modify: `services/admin-ui/app/components/AgentProfileSettings.tsx` (Documents tab switch)
- Create: `services/admin-ui/app/lib/reader.ts`
- Create: `services/admin-ui/app/components/ReaderAssistantCard.tsx`
- Modify: `services/admin-ui/app/components/AgentsView.tsx` (render card above the table)
- Modify: `services/admin-ui/app/lib/operations.ts` (`OperationsRun.context.grounding`)
- Create: `services/admin-ui/app/components/GroundingSources.tsx`
- Modify: `services/admin-ui/app/components/AgentTestView.tsx` (append sources to assistant `details`)
- Modify: `services/admin-ui/app/lib/api.ts` (`AuditRow`), `services/admin-ui/app/components/AdminSessionView.tsx`
- Test: `services/admin-ui/app/components/__tests__/readerAssistant.test.tsx`, `services/admin-ui/app/components/__tests__/adminViews.test.tsx` (update transcript test)

**Interfaces:**
- Consumes: `GET/PUT /api/reader/settings` (Task 5), `documents_grounded` (Task 2), `context.grounding` (Task 4), Activity rows (Task 7).
- Produces: `GroundingSources({ sources }: { sources: GroundingSource[] })`, `type GroundingSource = { n: number; path: string; heading_path: string; anchor: string | null }`.

- [ ] **Step 1: Write the failing tests**

`readerAssistant.test.tsx`:

```tsx
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { afterEach, expect, test, vi } from 'vitest';
import ReaderAssistantCard from '../ReaderAssistantCard';
import GroundingSources from '../GroundingSources';

afterEach(() => vi.unstubAllGlobals());
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const settings = { knowledge_agent_id: 'search', revision: 1, updated_at: null, candidates: [
  { id: 'search', name: 'Search agent', enabled: true, violations: [] },
  { id: 'operations', name: 'SSH agent', enabled: true, violations: ['uses SSH'] },
  { id: 'docs', name: 'Docs helper', enabled: true, violations: [] },
] };

test('shows the Reader agent, disables violating agents and saves a new choice', async () => {
  const fetchMock = vi.fn(async (_url: string, init?: RequestInit) =>
    init?.method === 'PUT' ? json({ ...settings, knowledge_agent_id: 'docs', revision: 2 }) : json(settings));
  vi.stubGlobal('fetch', fetchMock);
  render(<MemoryRouter><ReaderAssistantCard /></MemoryRouter>);
  const select = await screen.findByLabelText('Reader agent');
  expect(screen.getByRole('option', { name: /SSH agent — uses SSH/ })).toBeDisabled();
  await userEvent.selectOptions(select, 'docs');
  await userEvent.click(screen.getByRole('button', { name: 'Save Reader agent' }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/reader/settings', expect.objectContaining({
    method: 'PUT', body: JSON.stringify({ knowledge_agent_id: 'docs', revision: 1 }) })));
  expect(await screen.findByText('Saved.')).toBeInTheDocument();
});

test('renders nothing when settings cannot be read', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json({ is_admin: true })));
  const { container } = render(<MemoryRouter><ReaderAssistantCard /></MemoryRouter>);
  await waitFor(() => expect(container).toBeEmptyDOMElement());
});

test('grounding sources list cited documents', () => {
  render(<GroundingSources sources={[{ n: 1, path: 'payments/retries.md', heading_path: 'payments', anchor: null }]} />);
  expect(screen.getByText('[1]')).toBeInTheDocument();
  expect(screen.getByText('payments/retries.md')).toBeInTheDocument();
});
```

In `adminViews.test.tsx`, replace the transcript test's row with the new shape and assertions:

```tsx
test("transcript renders q/a turns with sources and a link to the agent session", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json({ rows: [
    { id: "r1", ts: "2026-10-02 11:00:00", user_email: "a@x.io", session_key: "s1", agent_id: "search",
      question: "q?", answer: "Refunds are async [1].", status: "completed", sources_used: ["docs"],
      sources: [{ n: 1, path: "payments/refunds.md", heading_path: "payments", anchor: null }],
      not_covered: true, escalated: false },
  ]})));
  render(<MemoryRouter><AdminSessionView sessionKey="s1" /></MemoryRouter>);
  await waitFor(() => expect(screen.getByText("payments/refunds.md")).toBeInTheDocument());
  expect(screen.getByText("not covered")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Open in agent session" }))
    .toHaveAttribute("href", "/agents/search?view=test&session_id=s1");
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/admin-ui && pnpm exec vitest run app/components/__tests__/readerAssistant.test.tsx app/components/__tests__/adminViews.test.tsx`
Expected: FAIL — cannot resolve `../ReaderAssistantCard`.

- [ ] **Step 3: Implement**

`lib/reader.ts`:

```ts
import { request } from './api';

export interface ReaderCandidate { id: string; name: string; enabled: boolean; violations: string[] }
export interface ReaderSettings { knowledge_agent_id: string | null; revision: number; updated_at: string | null; candidates: ReaderCandidate[] }

export const getReaderSettings = () => request<ReaderSettings>('/api/reader/settings');
export const saveReaderSettings = (knowledge_agent_id: string, revision: number) =>
  request<ReaderSettings>('/api/reader/settings', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ knowledge_agent_id, revision }),
  });
```

(`request` is the shared fetch helper exported from `lib/api.ts`.)

`components/ReaderAssistantCard.tsx`:

```tsx
import { useEffect, useState } from 'react';
import { getReaderSettings, saveReaderSettings, type ReaderSettings } from '../lib/reader';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Button } from '~/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '~/components/ui/card';
import { Field, FieldDescription, FieldLabel } from '~/components/ui/field';

export default function ReaderAssistantCard() {
  const [settings, setSettings] = useState<ReaderSettings | null>(null);
  const [choice, setChoice] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  useEffect(() => {
    let live = true;
    getReaderSettings().then(data => {
      if (live && Array.isArray(data?.candidates)) { setSettings(data); setChoice(data.knowledge_agent_id ?? ''); }
    }).catch(() => undefined);
    return () => { live = false; };
  }, []);
  if (!settings) return null;
  async function save() {
    setBusy(true); setError(''); setNotice('');
    try { const saved = await saveReaderSettings(choice, settings!.revision); setSettings(saved); setNotice('Saved.'); }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not save the Reader agent.'); }
    finally { setBusy(false); }
  }
  return <Card>
    <CardHeader>
      <CardTitle>Reader assistant</CardTitle>
      <CardDescription>The agent that answers questions in the Reader. It must use only documents, skills and services, with “Answer only from documents” on.</CardDescription>
    </CardHeader>
    <CardContent className="flex flex-col gap-3">
      {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
      <Field>
        <FieldLabel htmlFor="reader-agent">Reader agent</FieldLabel>
        <select id="reader-agent" className="h-9 rounded-md border bg-background px-2 text-sm" value={choice} disabled={busy} onChange={event => setChoice(event.target.value)}>
          {settings.knowledge_agent_id === null && <option value="">Not configured</option>}
          {settings.candidates.map(agent => <option key={agent.id} value={agent.id} disabled={agent.violations.length > 0}>
            {agent.violations.length ? `${agent.name} — ${agent.violations.join(', ')}` : agent.name}
          </option>)}
        </select>
        <FieldDescription>Changing the agent affects new Reader conversations; existing ones keep their agent.</FieldDescription>
      </Field>
      <div className="flex items-center gap-3">
        <Button type="button" size="sm" disabled={busy || !choice || choice === settings.knowledge_agent_id} onClick={() => void save()}>Save Reader agent</Button>
        {notice && <span role="status" className="text-sm text-muted-foreground">{notice}</span>}
      </div>
    </CardContent>
  </Card>;
}
```

In `AgentsView.tsx`, import `ReaderAssistantCard` and render `<ReaderAssistantCard />` as the first child of the page's root element (above the agents table card).

`components/GroundingSources.tsx`:

```tsx
export type GroundingSource = { n: number; path: string; heading_path: string; anchor: string | null };

export default function GroundingSources({ sources }: { sources: GroundingSource[] }) {
  if (!sources.length) return null;
  return <ol aria-label="Cited sources" className="mt-2 flex flex-col gap-1 text-sm">
    {sources.map(source => <li key={source.n}><span>[{source.n}]</span>{' '}<span className="font-mono">{source.path}</span>{' '}<span className="text-muted-foreground">{source.heading_path}</span></li>)}
  </ol>;
}
```

`lib/operations.ts`: inside the `OperationsRun.context` type add
`grounding?: { sources: { n: number; path: string; heading_path: string; anchor: string | null }[]; not_covered: boolean } | null`.

`AgentTestView.tsx`: in the assistant message object, change `details:` so that sources render whenever present. Wrap the current expression:

```tsx
      details: <>
        {run.context.grounding?.sources?.length ? <GroundingSources sources={run.context.grounding.sources} /> : null}
        {/* existing details expression stays here unchanged */}
      </>,
```

`lib/api.ts` `AuditRow` becomes:

```ts
export interface AuditRow {
  id: string; ts: string; user_email: string; session_key: string; agent_id: string; question: string; answer: string;
  status: string; sources_used: string[]; sources: { n: number; path: string; heading_path: string; anchor: string | null }[];
  not_covered: boolean; escalated: boolean;
}
```

`AdminSessionView.tsx`: import `Link` from `react-router` and `GroundingSources`. Inside each row's `CardContent`, after `<Markdown content={row.answer} />`, add `<GroundingSources sources={row.sources} />`. Render one link per session, directly above the rows list (inside the `rows.length > 0` branch, before `rows.map`):

```tsx
<Link className="text-sm underline underline-offset-4" to={`/agents/${encodeURIComponent(rows[0].agent_id)}?view=test&session_id=${rows[0].session_key}`}>Open in agent session</Link>
```

`lib/profiles.ts`: add `documents_grounded: boolean;` to `AgentProfile` and include `documents_grounded` in both the destructuring and the PUT body of the save function.

`AgentProfileSettings.tsx`: in the Documents section, after the include-subfolders block, add:

```tsx
        <Field orientation="horizontal"><Switch id="documents-grounded" disabled={busy || !draft.documents_enabled} checked={draft.documents_grounded} onCheckedChange={documents_grounded => change({ documents_grounded })} /><div><FieldLabel htmlFor="documents-grounded">Answer only from documents</FieldLabel><FieldDescription>Search the knowledge base, cite every claim, and report questions the documents do not cover. Required for the Reader assistant.</FieldDescription></div></Field>
```

Update any test fixtures typed as `AgentProfile`/`AgentSummary` (for example in `agentTabs.test.tsx`) to include `documents_grounded: false`.

- [ ] **Step 4: Run tests**

Run: `cd services/admin-ui && pnpm test`
Expected: all pass; `pnpm exec tsc --noEmit` (or the project's typecheck script) reports no errors.

- [ ] **Step 5: Commit**

```bash
git add services/admin-ui/app
git commit -m "feat(admin-ui): Reader assistant card, grounding switch and cited sources"
```

---

### Task 9: docs-wiki adapter

**Files:**
- Create: `services/docs-wiki/src/docs_wiki/reader_client.py`
- Modify: `services/docs-wiki/src/docs_wiki/search_api.py` (`build_api(..., reader=None, gaps_dir=None)`, reader-mode routes)
- Modify: `services/docs-wiki/src/docs_wiki/webapp.py` (construct `ReaderClient`, pass `code=None` and `gaps_dir`)
- Modify: `deploy/compose.yaml` (docs-wiki env)
- Test: `services/docs-wiki/tests/test_reader_chat.py`; update `tests/test_search_api.py::test_chat_todo_has_no_outbound_calls_or_session` only if its fixture now passes a reader (it should not — leave TODO behavior for `reader=None`).

**Interfaces:**
- Consumes: the internal endpoints (Task 6).
- Produces:
  - `ReaderClient(base_url: str, key: str, transport: httpx.AsyncBaseTransport | None = None)` with async methods:
    - `run(subject, email, body: dict) -> AsyncIterator[tuple[str, dict]]` (raises `ReaderError(status, message)` before streaming)
    - `sessions(subject, email) -> list[dict]`
    - `transcript(subject, email, key) -> dict`
    - `delete(subject, email, key) -> None`
  - `UNAVAILABLE = 'Chat is temporarily unavailable.'`

- [ ] **Step 1: Write the failing tests**

```python
"""Reader chat forwards to admin-api and maps events to the Reader protocol."""
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from docs_wiki import gaps, search_api
from docs_wiki.reader_client import ReaderClient

SESSION = '0b6f1e1a-1111-4a5b-8c9d-000000000001'


def sse_body(*events):
    return ''.join(f'event: {name}\ndata: {json.dumps(data)}\n\n' for name, data in events).encode()


@pytest.fixture
def upstream():
    state = {'events': [], 'status': 200, 'error': None, 'requests': []}
    def handle(request):
        state['requests'].append(request)
        if state['status'] != 200:
            return httpx.Response(state['status'], json={'error': {'message': state['error']}})
        if request.url.path == '/internal/reader/runs':
            return httpx.Response(200, content=sse_body(*state['events']), headers={'Content-Type': 'text/event-stream'})
        if request.url.path == '/internal/reader/sessions':
            return httpx.Response(200, json={'sessions': [{'id': SESSION, 'title': 'How many retries?', 'status': 'active',
                'created_at': '2026-10-02T10:00:00Z', 'last_activity_at': '2026-10-02T10:05:00Z'}]})
        if request.url.path == f'/internal/reader/sessions/{SESSION}':
            if request.method == 'DELETE':
                return httpx.Response(204)
            return httpx.Response(200, json={'id': SESSION, 'title': 'q', 'status': 'active', 'folders': ['payments'],
                'include_subfolders': True, 'messages': [{'role': 'user', 'content': 'q'},
                {'role': 'assistant', 'content': 'a [1]', 'sources': [{'n': 1, 'path': 'p/r.md', 'heading_path': 'p', 'anchor': None}], 'not_covered': False}]})
        raise AssertionError(request.url)
    return state, httpx.MockTransport(handle)


@pytest.fixture
def client(tmp_path, upstream):
    _, transport = upstream
    app = FastAPI()
    reader = ReaderClient('http://admin.test', 'reader-key', transport=transport)
    app.include_router(search_api.build_api('/api/docs', tmp_path / 'unused', None, None, reader=reader, gaps_dir=tmp_path / 'gaps'))
    with TestClient(app, headers={'X-Forwarded-User': 'alice', 'X-Forwarded-Email': 'alice@example.test',
                                  'X-Forwarded-Groups': 'docs-admins'}) as test_client:
        yield test_client


def parse(response):
    events, name = [], None
    for line in response.text.splitlines():
        if line.startswith('event: '):
            name = line[7:]
        elif line.startswith('data: '):
            events.append((name, json.loads(line[6:])))
    return events


def test_answer_maps_to_reader_events(client, upstream, tmp_path):
    state, _ = upstream
    grounding = {'sources': [{'n': 1, 'path': 'p/r.md', 'heading_path': 'p', 'anchor': None}], 'not_covered': False,
                 'near_misses': [], 'results': 1, 'reason': None}
    state['events'] = [('session', {'id': SESSION}), ('activity', {'tool': 'search_documents', 'argument': '{"query":"retries"}'}),
                       ('completed', {'status': 'completed', 'answer': 'Three [1].', 'error': '', 'grounding': grounding})]
    response = client.post('/api/docs/chat', json={'question': 'How many retries?', 'context': {'folders': ['payments'], 'include_subfolders': False, 'repositories': None}})
    assert response.status_code == 200
    assert parse(response) == [
        ('session', {'key': SESSION}), ('tool', {'name': 'search_documents', 'argument': '{"query":"retries"}'}),
        ('sources', {'sources': grounding['sources']}),
        ('done', {'text': 'Three [1].', 'not_covered': False, 'escalation_offered': False, 'sources_used': ['docs']})]
    sent = state['requests'][0]
    assert sent.headers['x-reader-key'] == 'reader-key' and sent.headers['x-reader-user'] == 'alice'
    assert 'x-forwarded-groups' not in sent.headers and 'x-forwarded-user' not in sent.headers
    assert json.loads(sent.content) == {'question': 'How many retries?', 'folders': ['payments'], 'include_subfolders': False}
    assert gaps.load_gaps(tmp_path / 'gaps') == ([], 0)


def test_not_covered_logs_a_gap(client, upstream, tmp_path):
    state, _ = upstream
    state['events'] = [('session', {'id': SESSION}), ('completed', {'status': 'completed', 'answer': 'Not documented.', 'error': '',
        'grounding': {'sources': [], 'not_covered': True, 'near_misses': [{'path': 'p/r.md', 'heading_path': 'p'}], 'results': 1, 'reason': 'x'}})]
    events = parse(client.post('/api/docs/chat', json={'question': 'Refund SLA?'}))
    assert events[-1] == ('done', {'text': 'Not documented.', 'not_covered': True, 'escalation_offered': False, 'sources_used': []})
    logged, skipped = gaps.load_gaps(tmp_path / 'gaps')
    assert skipped == 0 and logged[0]['question'] == 'Refund SLA?' and logged[0]['near_misses'][0]['path'] == 'p/r.md'
    assert 'session_ref' in logged[0] and SESSION not in json.dumps(logged)


def test_failed_turn_becomes_error(client, upstream, tmp_path):
    state, _ = upstream
    state['events'] = [('completed', {'status': 'failed', 'answer': '', 'error': 'The agent exceeded the time limit. Send a smaller task.', 'grounding': None})]
    assert parse(client.post('/api/docs/chat', json={'question': 'q', 'session': SESSION}))[-1] == (
        'error', {'message': 'The agent exceeded the time limit. Send a smaller task.'})


def test_stream_without_completion_reports_unavailable(client, upstream, tmp_path):
    state, _ = upstream
    state['events'] = [('session', {'id': SESSION})]
    assert parse(client.post('/api/docs/chat', json={'question': 'q'}))[-1] == ('error', {'message': 'Chat is temporarily unavailable.'})
    assert gaps.load_gaps(tmp_path / 'gaps') == ([], 0)


@pytest.mark.parametrize('status, message, expected_status, expected', [
    (409, 'Wait for the current answer.', 409, 'Wait for the current answer.'),
    (404, 'Unknown conversation', 404, 'Unknown conversation'),
    (401, 'Invalid reader key.', 503, 'Chat is temporarily unavailable.'),
    (500, 'boom', 503, 'Chat is temporarily unavailable.'),
])
def test_upstream_errors(client, upstream, status, message, expected_status, expected):
    state, _ = upstream
    state['status'], state['error'] = status, message
    response = client.post('/api/docs/chat', json={'question': 'q'})
    assert response.status_code == expected_status and response.json()['error']['message'] == expected


def test_unreachable_admin_api(tmp_path):
    def down(request):
        raise httpx.ConnectError('down')
    app = FastAPI()
    app.include_router(search_api.build_api('/api/docs', tmp_path, None, None,
        reader=ReaderClient('http://admin.test', 'k', transport=httpx.MockTransport(down)), gaps_dir=tmp_path))
    with TestClient(app, headers={'X-Forwarded-User': 'alice'}) as test_client:
        response = test_client.post('/api/docs/chat', json={'question': 'q'})
    assert response.status_code == 503 and response.json()['error']['message'] == 'Chat is temporarily unavailable.'


def test_history_transcript_and_delete(client):
    sessions = client.get('/api/docs/chats').json()['sessions']
    assert sessions == [{'session_key': SESSION, 'first_question': 'How many retries?',
                         'first_ts': '2026-10-02T10:00:00Z', 'last_ts': '2026-10-02T10:05:00Z'}]
    transcript = client.get(f'/api/docs/chats/{SESSION}').json()
    assert transcript['session'] == SESSION
    assert transcript['context'] == {'folders': ['payments'], 'include_subfolders': True, 'repositories': None}
    assert transcript['sources'] == {'docs': True, 'codex': False}
    assert transcript['messages'][1] == {'role': 'assistant', 'content': 'a [1]', 'sources_used': ['docs'],
        'sources': [{'n': 1, 'path': 'p/r.md', 'heading_path': 'p', 'anchor': None}], 'not_covered': False}
    assert client.delete(f'/api/docs/chats/{SESSION}').status_code == 204


def test_identity_required(client):
    client.headers.pop('X-Forwarded-User')
    assert client.post('/api/docs/chat', json={'question': 'q'}).status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/docs-wiki && uv run --frozen pytest tests/test_reader_chat.py -q`
Expected: FAIL — `ModuleNotFoundError: docs_wiki.reader_client`.

- [ ] **Step 3: Implement `reader_client.py`**

```python
"""Client for admin-api's internal Reader conversation endpoints."""
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

UNAVAILABLE = 'Chat is temporarily unavailable.'
PASS_THROUGH = {400, 404, 409}


class ReaderError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


class ReaderClient:
    def __init__(self, base_url: str, key: str, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url, self.key, self.transport = base_url, key, transport

    def _client(self, subject: str, email: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, transport=self.transport, timeout=httpx.Timeout(30, read=330),
            headers={'X-Reader-Key': self.key, 'X-Reader-User': subject, 'X-Reader-Email': email})

    @staticmethod
    async def _raise(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        await response.aread()
        if response.status_code in PASS_THROUGH:
            try:
                message = response.json()['error']['message']
            except (ValueError, KeyError, TypeError):
                message = UNAVAILABLE
            raise ReaderError(response.status_code, message)
        raise ReaderError(503, UNAVAILABLE)

    async def run(self, subject: str, email: str, body: dict[str, Any]) -> AsyncIterator[tuple[str, dict]]:
        client = self._client(subject, email)
        try:
            response = await client.send(client.build_request('POST', '/internal/reader/runs', json=body), stream=True)
            await self._raise(response)
        except httpx.HTTPError:
            await client.aclose()
            raise ReaderError(503, UNAVAILABLE) from None
        except ReaderError:
            await client.aclose()
            raise

        async def events():
            name, data = '', ''
            try:
                async for line in response.aiter_lines():
                    if line.startswith('event: '):
                        name = line[7:]
                    elif line.startswith('data: '):
                        data += line[6:]
                    elif line == '' and name:
                        try:
                            yield name, json.loads(data)
                        except ValueError:
                            pass
                        name, data = '', ''
            except httpx.HTTPError:
                return
            finally:
                await response.aclose()
                await client.aclose()
        return events()

    async def _json(self, subject: str, email: str, method: str, path: str) -> Any:
        async with self._client(subject, email) as client:
            try:
                response = await client.request(method, path)
            except httpx.HTTPError:
                raise ReaderError(503, UNAVAILABLE) from None
            await self._raise(response)
            return None if response.status_code == 204 else response.json()

    async def sessions(self, subject: str, email: str) -> list[dict]:
        return (await self._json(subject, email, 'GET', '/internal/reader/sessions'))['sessions']

    async def transcript(self, subject: str, email: str, key: str) -> dict:
        return await self._json(subject, email, 'GET', f'/internal/reader/sessions/{key}')

    async def delete(self, subject: str, email: str, key: str) -> None:
        await self._json(subject, email, 'DELETE', f'/internal/reader/sessions/{key}')
```

- [ ] **Step 4: Reader-mode routes in `search_api.py`**

Change the signature to `def build_api(prefix, state_root, rag, code, audit_dir=None, profiles=False, reader=None, gaps_dir=None)`. Add imports `import json`, `from fastapi.responses import StreamingResponse`, `from docs_wiki import gaps`, `from docs_wiki.reader_client import ReaderError, UNAVAILABLE`. When `reader is not None`, register the reader routes below **instead of** the existing `/chats`, `/chats/{key}` (GET/DELETE) and `/chat` routes; `/status`, `/context` and `/chat/{key}/escalate` stay as they are.

```python
    def identity(request: Request) -> tuple[str, str]:
        subject = request.headers.get('x-forwarded-user')
        if not subject:
            raise HTTPException(403, 'Authenticated user identity required')
        return subject, request.headers.get('x-forwarded-email', subject)

    def frame(name: str, data: dict) -> str:
        return f'event: {name}\ndata: {json.dumps(data)}\n\n'

    def session_ref(key: str) -> str:
        return hashlib.sha256(key.encode()).hexdigest()[:12]

    if reader is not None:
        @router.post('/chat')
        async def reader_chat(body: ChatInput, request: Request):
            subject, email = identity(request)
            if not body.question.strip():
                raise HTTPException(400, 'Enter a question')
            payload = {'question': body.question, 'folders': body.context.folders,
                       'include_subfolders': body.context.include_subfolders}
            if body.session is not None:
                payload['session_id'] = body.session
            try:
                upstream = await reader.run(subject, email, payload)
            except ReaderError as exc:
                raise HTTPException(exc.status, exc.message) from None

            async def stream():
                key = body.session
                finished = False
                async for name, data in upstream:
                    if name == 'session':
                        key = data['id']
                        yield frame('session', {'key': key})
                    elif name == 'activity':
                        yield frame('tool', {'name': data['tool'], 'argument': data.get('argument', '')})
                    elif name == 'completed':
                        finished = True
                        if data['status'] != 'completed':
                            yield frame('error', {'message': data.get('error') or UNAVAILABLE})
                            break
                        found = data.get('grounding') or {}
                        sources = found.get('sources', [])
                        yield frame('sources', {'sources': sources})
                        yield frame('done', {'text': data['answer'], 'not_covered': bool(found.get('not_covered')),
                                             'escalation_offered': False, 'sources_used': ['docs'] if sources else []})
                        if found.get('not_covered') and gaps_dir is not None:
                            gaps.append_gap(gaps_dir, body.question, found.get('near_misses', []),
                                            results=found.get('results'), session_ref=session_ref(key) if key else None)
                        break
                if not finished:
                    yield frame('error', {'message': UNAVAILABLE})
            return StreamingResponse(stream(), media_type='text/event-stream',
                                     headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})

        @router.get('/chats')
        async def reader_history(request: Request):
            subject, email = identity(request)
            try:
                rows = await reader.sessions(subject, email)
            except ReaderError as exc:
                raise HTTPException(exc.status, exc.message) from None
            return {'sessions': [{'session_key': row['id'], 'first_question': row['title'],
                                  'first_ts': row['created_at'], 'last_ts': row['last_activity_at']} for row in rows]}

        @router.get('/chats/{key}')
        async def reader_transcript(key: str, request: Request):
            subject, email = identity(request)
            try:
                saved = await reader.transcript(subject, email, key)
            except ReaderError as exc:
                raise HTTPException(exc.status, exc.message) from None
            messages = [message if message['role'] == 'user' else {
                'role': 'assistant', 'content': message['content'],
                'sources_used': ['docs'] if message.get('sources') else [],
                'sources': message.get('sources', []), 'not_covered': message.get('not_covered', False)}
                for message in saved['messages']]
            return {'session': saved['id'], 'messages': messages,
                    'context': {'folders': saved['folders'], 'include_subfolders': saved['include_subfolders'], 'repositories': None},
                    'sources': {'docs': True, 'codex': False}}

        @router.delete('/chats/{key}', status_code=204)
        async def reader_delete(key: str, request: Request):
            subject, email = identity(request)
            try:
                await reader.delete(subject, email, key)
            except ReaderError as exc:
                raise HTTPException(exc.status, exc.message) from None
            return Response(status_code=204)
```

Wrap the existing `transcript`, `delete`, `history` and `chat` route definitions in `if reader is None:` so they are only registered without a reader. When `reader is not None`, `ChatInput.session` may hold a UUID; no `sessions.KEY_RE` check applies.

- [ ] **Step 5: Wire `webapp.py` and compose**

In `webapp.py`, inside `if workspace_rag is not None:` before the per-app loop:

```python
        reader = None
        reader_url, reader_key = os.environ.get('READER_AGENT_URL'), os.environ.get('READER_AGENT_KEY')
        if reader_url and reader_key:
            from docs_wiki.reader_client import ReaderClient
            reader = ReaderClient(reader_url, reader_key)
```

and change the per-app `build_search_api(...)` call to pass `None if reader is not None else code_client` as `code`, plus `reader=reader, gaps_dir=state_root(server) / name`.

`deploy/compose.yaml`, `docs-wiki.environment`: add

```yaml
      READER_AGENT_URL: http://admin-api:8766
      READER_AGENT_KEY: ${READER_AGENT_KEY:?Run make setup}
```

Do not add `admin-api` to docs-wiki's `depends_on`.

- [ ] **Step 6: Run tests**

Run:
```
cd services/docs-wiki && uv run --frozen pytest -q
cd ../.. && make compose-validate
```
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add services/docs-wiki/src/docs_wiki/reader_client.py services/docs-wiki/src/docs_wiki/search_api.py services/docs-wiki/src/docs_wiki/webapp.py services/docs-wiki/tests/test_reader_chat.py deploy/compose.yaml
git commit -m "feat(reader): forward chat to the admin-configured Reader agent"
```

---

### Task 10: Reader UI

**Files:**
- Modify: `services/docs-wiki-ui/app/lib/api.ts` (`streamChat`)
- Modify: `packages/chat-ui/types.ts` (`Transcript.messages[]` adds `sources?`, `not_covered?`)
- Modify: `packages/chat-ui/ChatPanel.tsx` (remove TODO footer; transcript reload maps `sources` and `not_covered`)
- Modify: `packages/chat-ui/AdvancedContext.tsx` (hide the Git fieldset when there are no repositories)
- Modify: `services/docs-wiki-ui/app/routes/chat.tsx` (delete dialog text)
- Test: `services/docs-wiki-ui/app/lib/__tests__/api.test.ts`, `services/docs-wiki-ui/app/components/__tests__/chatPanel.test.tsx`

**Interfaces:**
- Consumes: the SSE events from Task 9 (`session`, `tool`, `sources`, `done`, `error`).
- Produces: `streamChat()` yields `ChatEvent`s.

- [ ] **Step 1: Write the failing tests**

In `api.test.ts`, replace the `test.each(['chat', 'escalate'])` TODO test with an escalate-only version (`escalateStream` is unchanged), and add:

```ts
function sseResponse(text: string, chunks = 1) {
  const bytes = new TextEncoder().encode(text);
  const size = Math.ceil(bytes.length / chunks);
  return new Response(new ReadableStream<Uint8Array>({ start(c) {
    for (let i = 0; i < bytes.length; i += size) c.enqueue(bytes.slice(i, i + size));
    c.close();
  } }), { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
}

test('streamChat parses reader events across chunk boundaries', async () => {
  const text = 'event: session\ndata: {"key":"s1"}\n\n' +
    'event: tool\ndata: {"name":"search_documents","argument":"{\\"query\\":\\"retries\\"}"}\n\n' +
    'event: sources\ndata: {"sources":[{"n":1,"path":"p/r.md","heading_path":"p","anchor":null}]}\n\n' +
    'event: done\ndata: {"text":"Three [1].","not_covered":false,"escalation_offered":false,"sources_used":["docs"]}\n\n';
  vi.stubGlobal('fetch', vi.fn(async () => sseResponse(text, 7)));
  const events = [];
  for await (const event of streamChat('shop', 'retries?')) events.push(event);
  expect(events.map(e => e.type)).toEqual(['session', 'tool', 'sources', 'done']);
  expect(events[0]).toEqual({ type: 'session', key: 's1' });
  expect(events[3]).toMatchObject({ type: 'done', text: 'Three [1].', not_covered: false });
});

test('streamChat reports an error when the stream ends without an answer', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => sseResponse('event: session\ndata: {"key":"s1"}\n\n')));
  const events = [];
  for await (const event of streamChat('shop', 'q')) events.push(event);
  expect(events.at(-1)).toEqual({ type: 'error', message: 'Chat is temporarily unavailable.' });
});
```

In `chatPanel.test.tsx`, add:

```tsx
test('the TODO footer is gone', () => {
  renderPanel();
  expect(screen.queryByText(/Agent execution is not implemented yet/)).not.toBeInTheDocument();
});
```

(`renderPanel` is the file's existing helper.) The chat-ui `ChatPanel.tsx` is re-exported for the Reader at `services/docs-wiki-ui/app/components/ChatPanel`; if the test file imports a different path, keep its import.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/docs-wiki-ui && pnpm exec vitest run app/lib/__tests__/api.test.ts app/components/__tests__/chatPanel.test.tsx`
Expected: FAIL — `streamChat` yields a single `todo` event.

- [ ] **Step 3: Implement**

In `app/lib/api.ts`, import `parseSse` from `./sse` and replace the last line of `streamChat` (`yield { type: "todo", text: await response.text() };`) with:

```ts
  if (!response.body) { yield { type: "error", message: "Chat is temporarily unavailable." }; return; }
  let finished = false;
  for await (const { event, data } of parseSse(response.body)) {
    const payload = (data ?? {}) as Record<string, unknown>;
    if (event === "done" || event === "error") finished = true;
    yield { type: event, ...payload } as ChatEvent;
    if (finished) return;
  }
  if (!finished) yield { type: "error", message: "Chat is temporarily unavailable." };
```

In `packages/chat-ui/types.ts`, change `Transcript.messages` to
`{ role: string; content: string; sources_used?: string[]; sources?: ChatSource[]; not_covered?: boolean }[]`.

In `packages/chat-ui/ChatPanel.tsx`:
- delete the `<p className="text-xs text-muted-foreground">Agent execution is not implemented yet. …</p>` footer;
- in the transcript mapping (`setTurns(t.messages.map(...))`), set `notCovered: m.role !== "user" && (m.not_covered ?? m.content.trim() === NOT_COVERED_SENTINEL)` and add `sources: m.sources`.

In `packages/chat-ui/AdvancedContext.tsx`, wrap the Git repositories `<FieldSet>` (the block containing `<FieldLegend>Git repositories</FieldLegend>`) in `{repositories.length > 0 && ( … )}`.

In `services/docs-wiki-ui/app/routes/chat.tsx`, find the delete confirmation (`grep -n "AlertDialogDescription" services/docs-wiki-ui/app/routes/chat.tsx`) and set its text to `Remove this conversation from your list?`.

- [ ] **Step 4: Run tests**

Run: `cd services/docs-wiki-ui && pnpm test && cd ../admin-ui && pnpm test`
Expected: all pass (admin-ui shares `packages/chat-ui`).

- [ ] **Step 5: Commit**

```bash
git add services/docs-wiki-ui/app packages/chat-ui
git commit -m "feat(reader-ui): stream cited answers and remove the TODO notice"
```

---

### Task 11: Smoke check and documentation

**Files:**
- Modify: `scripts/smoke.py`
- Modify: `docs/user-guide.md`, `docs/agents.md`, `README.md`

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Add the smoke check**

In `scripts/smoke.py` `main()`, after the "independent reader/admin logins" PASS line, add:

```python
    # The internal Reader API is not routed by the admin gateway (nginx only proxies /api/).
    with admin.open(admin_origin + "/internal/reader/sessions", timeout=10) as response:
        if "application/json" in response.headers.get("Content-Type", ""):
            raise RuntimeError("The admin gateway exposes the internal Reader API")
    rejected = subprocess.run(
        [*compose, "exec", "-T", "docs-wiki", "/app/.venv/bin/python", "-c",
         "import urllib.request,urllib.error\n"
         "try:\n urllib.request.urlopen(urllib.request.Request('http://admin-api:8766/internal/reader/sessions',"
         "headers={'X-Reader-User':'smoke'}),timeout=5)\nexcept urllib.error.HTTPError as e:\n print(e.code)"],
        check=True, capture_output=True, text=True).stdout.strip()
    if rejected != "401":
        raise RuntimeError(f"Internal Reader API accepted a request without its key (HTTP {rejected or 'success'})")
    print("PASS: internal Reader API requires its key and is not exposed by the admin gateway")
```

- [ ] **Step 2: Run the stack and smoke**

Run: `make up && make smoke`
Expected: all PASS lines, including the new one.

- [ ] **Step 3: Manual verification (requires the Search agent's model)**

1. Open http://localhost:8180/docs/chat and ask a question covered by a processed RAG folder. Expected: activity lines ("searching: …"), then an answer with `[n]` links that open the right document.
2. Ask something the docs don't cover. Expected: "not covered" badge, the explanation of what's missing, and a new entry at `/docs/gaps`.
3. Open http://localhost:8183/activity, then the user and the session. Expected: the questions, answers and sources; **Open in agent session** opens the Search agent's Test tab with the conversation, where **Add feedback → Suggest improvements** works.
4. In Admin → Agents → Search agent → Documents, turn **Answer only from documents** off and save. Expected: the Reader assistant card shows the reason, and a new Reader question returns "The assistant's configuration isn't allowed in the Reader." Turn it back on.

- [ ] **Step 4: Update documentation**

- `docs/user-guide.md`:
  - **§4 Chat:** replace the TODO description with how answers, citations and "not covered" work, the 7-day conversation lifetime, and that deleting removes a conversation from your list only.
  - **§4 Gaps:** say that new gaps are recorded when an answer is not covered.
  - **§5.11 Agents:** add the Documents-tab **Answer only from documents** switch and the **Reader assistant** card.
  - **§5.16 Activity:** say that it shows Reader conversations from the database, with **Open in agent session**.
  - **§8:** remove the Reader-chat and Activity/Gaps limitations; add "answers appear when complete (no word-by-word streaming)".
  - **Appendix A:** add the `search_documents`, `read_document` and `report_not_covered` tools.
  - **Appendix B:** remove the now-fixed rows.
- `docs/agents.md`: replace "connecting the user-facing chat is a separate step" with a short section on Reader conversations: `channel = 'reader'`, 7-day idle timeout, owner-only access through `/internal/reader/*`, and the `READER_AGENT_KEY` secret. Remove the Reader rows from the TODO route table.
- `README.md`: replace the sentence that says Chat returns `TODO` with: "Reader chat answers from the knowledge base using the agent chosen in Admin → Agents → Reader assistant; agent Test and the multi-agent board run models."

- [ ] **Step 5: Final verification**

Run: `make test`
Expected: exits 0.

- [ ] **Step 6: Commit**

```bash
git add scripts/smoke.py docs/user-guide.md docs/agents.md README.md
git commit -m "docs: Reader chat, Reader assistant and smoke check for the internal API"
```
