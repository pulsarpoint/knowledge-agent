# Prompt Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prompts become a tagged library under Administration: `tags` on `ka.documentation_prompts`, tag filter and
tag suggestions in the API and UI, `input_kind` retired from the API and UI, and the page moved to `/prompts`.

**Architecture:** Migration 0096 adds `tags` with a CHECK, backfills from `input_kind`, gives `input_kind` a default and
adds a BEFORE INSERT trigger that tags untagged inserts (so per-workspace seed functions keep working). The prompts API
(`documentation_prompts.py`) swaps `input_kind` for `tags`, adds `?tag=` and `GET /tags`. The admin-ui moves the pages
to `/prompts…` with redirects, adds a tags input and filters.

**Tech Stack:** PostgreSQL 17 + golang-migrate SQL, FastAPI + psycopg3 (admin-api), React Router 7 + shadcn/ui + vitest
(admin-ui).

**Spec:** `docs/superpowers/specs/2026-10-10-prompt-library-design.md`

## Global Constraints

- Work only in the worktree `.claude/worktrees/prompt-library` on branch `feature/prompt-library`.
- Never run `make` targets. Never touch `.env` or anything under `.local`. Never `git stash`. Never touch the running
  Docker stack's database (tests use their own disposable PostgreSQL through the `store` fixture).
- Never `git add .superpowers`; never commit `node_modules`.
- Every commit message ends with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Backend tests: `cd services/admin-api && uv run pytest -q <files>`. UI: `cd services/admin-ui && npx vitest run
  <files>` and `npx tsc --noEmit` (if `node_modules` is missing, run `pnpm install --frozen-lockfile` there — the UI uses
  pnpm, not npm).
- A tag matches `^[a-z0-9][a-z0-9-]{0,39}$`; at most 10 tags per prompt; no duplicates.
- Messages exactly: invalid tag → 400 "Tags use lowercase letters, digits and -, up to 40 characters."; too many → 400
  "Use at most 10 tags."
- The API path stays `/api/documentation/prompts`. The table stays `ka.documentation_prompts`. `input_kind` stays in the
  table (with `DEFAULT 'mixed'`) but the API neither accepts nor returns it.
- Tags live on the prompt, not on a version: changing only tags never creates a version.
- UI addresses: `/prompts`, `/prompts/new`, `/prompts/:promptId`; `/documentation/prompts`, `/documentation/prompts/new`,
  `/documentation/prompts/:promptId` redirect (replace) to the matching new address.
- Known pre-existing backend failures (not caused by this work): `test_code_graphs_config`, `test_code_processing`,
  `test_code_graph_sdk` (3 errors; the worktree has no code-graph `node_modules`), `test_deploy` docs test,
  `test_environments.py::test_migration_reapply_and_populated_rollback_protection`,
  `test_agent_tasks::test_the_limit_comes_from_the_environment` (timing flake). A docker-port `IndexError` in the
  postgres fixture is a flake: re-run once.
- The app turns request-validation errors (pydantic, bad query types) into 400, not 422.

## Review Focus

1. **Mixed-case or padded tags** (`" Server "`): stored as `server`, not refused. Tested in Task 2.
2. **A prompt saved from the form with an unchanged prompt text but new tags**: saved, no new version. Tested in Task 2.
3. **Seeded prompts of a workspace created after the upgrade** carry `documentation` + their kind, so the tag filter
   finds them like old ones. Tested in Task 1.
4. **Bookmarks of `/documentation/prompts/<id>`** open the same prompt at `/prompts/<id>`. Tested in Task 3.
5. **Generate documents and tags of prompts it cannot use** (disabled or without an active version): those tags are
   not offered in its tag filter, so a filter never empties the list; "Write my own" is always there. Tested in Task 3.

---

## File structure

| File | Responsibility |
|---|---|
| `db/migrations/0096_prompt_tags.up.sql` / `.down.sql` | `tags` column, CHECK, index, backfill, default, seed trigger |
| `db/tests/run.sh` | 0096 down check and final check |
| `services/admin-api/src/knowledge_admin/documentation_prompts.py` | tags in input/output, `?tag=`, `GET /tags`, no `input_kind` |
| `services/admin-ui/app/lib/documents.ts` | prompt types and calls with tags |
| `services/admin-ui/app/components/TagsInput.tsx` (new) | the tags field |
| `services/admin-ui/app/components/DocumentationPromptsView.tsx` | list with tag badges and filter; editor with tags; links to `/prompts…` |
| `services/admin-ui/app/routes.ts`, `routes/promptLibrary*.tsx` (new), `routes/documentationPrompt*.tsx` (become redirects) | addresses |
| `services/admin-ui/app/components/AdminLayout.tsx` | Prompts in Administration, title and description |
| `services/admin-ui/app/components/DocumentGenerationForm.tsx` | picker shows tags, tag filter |
| `docs/user-guide.md`, `docs/migrations.md` | docs |

---

### Task 1: Migration 0096 — prompt tags

**Files:**
- Create: `db/migrations/0096_prompt_tags.up.sql`, `db/migrations/0096_prompt_tags.down.sql`
- Modify: `db/tests/run.sh`, `docs/migrations.md`
- Test: `services/admin-api/tests/test_prompt_tags_migration.py` (new)

**Interfaces:**
- Produces: column `ka.documentation_prompts.tags text[] NOT NULL DEFAULT '{}'`, constraint `documentation_prompts_tags`,
  helper `ka.documentation_prompt_tags_unique(text[])`, index `documentation_prompts_tags_gin`, function
  `ka.documentation_prompt_default_tags()`, trigger
  `documentation_prompt_default_tags`; `input_kind` default `'mixed'`.

- [ ] **Step 1: Write the failing tests**

Create `services/admin-api/tests/test_prompt_tags_migration.py`:

```python
"""0096: prompts carry tags; input_kind is retired but kept for the seed functions."""
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0096_prompt_tags.up.sql', '0096_prompt_tags.down.sql'


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def add(conn, project, name, **columns):
    columns = {'project_id': project, 'name': name, **columns}
    return conn.execute(f"INSERT INTO ka.documentation_prompts({','.join(columns)}) VALUES ({','.join(['%s'] * len(columns))}) RETURNING *",
                        list(columns.values())).fetchone()


def test_seeded_prompts_of_a_new_workspace_are_tagged(store):
    with store.connect() as conn:
        project = store.project(conn, f'shop-{uuid4().hex[:6]}')  # a new project runs the seed triggers
        rows = conn.execute('SELECT input_kind,tags FROM ka.documentation_prompts WHERE project_id=%s', (project,)).fetchall()
    assert rows, 'the seed functions create prompts for a new workspace'
    assert all(row['tags'] == ['documentation', row['input_kind']] for row in rows)


def test_untagged_inserts_get_tags_and_tagged_ones_keep_theirs(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        assert add(conn, project, 'Old style', input_kind='server')['tags'] == ['documentation', 'server']
        assert add(conn, project, 'No kind')['tags'] == ['documentation', 'mixed']
        assert add(conn, project, 'Tagged', tags=['server-discovery'])['tags'] == ['server-discovery']


@pytest.mark.parametrize('tags', [['Server'], ['-x'], ['a' * 41], ['has space'], ['a', 'a'], [f't{i}' for i in range(11)]])
def test_bad_tags_are_refused(store, tags):
    with pytest.raises(psycopg.errors.CheckViolation), store.connect() as conn:
        add(conn, store.project(conn, 'shop'), f'Bad {uuid4().hex[:6]}', tags=tags)


def test_down_and_up_backfill_from_input_kind(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        run(conn, DOWN)
        assert conn.execute("""SELECT count(*) AS n FROM information_schema.columns WHERE table_schema='ka'
            AND table_name='documentation_prompts' AND column_name='tags'""").fetchone()['n'] == 0
        prompt = conn.execute("""INSERT INTO ka.documentation_prompts(project_id,name,input_kind)
            VALUES (%s,'Runbook','service') RETURNING id""", (project,)).fetchone()['id']
        run(conn, UP)
        assert conn.execute('SELECT tags FROM ka.documentation_prompts WHERE id=%s', (prompt,)).fetchone()['tags'] == ['documentation', 'service']
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd services/admin-api && uv run pytest -q tests/test_prompt_tags_migration.py`
Expected: FAIL (`column "tags" does not exist`, missing migration file).

- [ ] **Step 3: Write `db/migrations/0096_prompt_tags.up.sql`**

```sql
BEGIN;
SET search_path = ka, public;

-- Prompts are a library organised by tags (lowercase letters, digits and -, up to 40 characters, at most 10, no
-- duplicates). Features find their prompts by tag. input_kind is retired: the per-workspace seed functions still
-- insert it, so the column stays with a default, and untagged inserts are tagged from it.
CREATE FUNCTION documentation_prompt_tags_unique(tags text[]) RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
    SELECT cardinality(tags) = (SELECT count(DISTINCT tag) FROM unnest(tags) AS tag)
$$;
ALTER TABLE documentation_prompts
    ADD COLUMN tags text[] NOT NULL DEFAULT '{}',
    ADD CONSTRAINT documentation_prompts_tags CHECK (
        cardinality(tags) <= 10
        AND array_to_string(tags, ',') ~ '^([a-z0-9][a-z0-9-]{0,39}(,|$))*$'
        AND documentation_prompt_tags_unique(tags)),
    ALTER COLUMN input_kind SET DEFAULT 'mixed';
CREATE INDEX documentation_prompts_tags_gin ON documentation_prompts USING gin (tags);
COMMENT ON COLUMN documentation_prompts.tags IS 'Free-text labels (lowercase, digits, -) that features and filters use to find prompts.';
COMMENT ON COLUMN documentation_prompts.input_kind IS 'Retired; kept for the seed functions. New prompts are classified by tags.';

UPDATE documentation_prompts SET tags = ARRAY['documentation', input_kind];

CREATE FUNCTION documentation_prompt_default_tags() RETURNS trigger LANGUAGE plpgsql SET search_path = ka, pg_temp AS $$
BEGIN
    IF cardinality(NEW.tags) = 0 THEN
        NEW.tags := ARRAY['documentation', NEW.input_kind];
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER documentation_prompt_default_tags BEFORE INSERT ON documentation_prompts
    FOR EACH ROW EXECUTE FUNCTION documentation_prompt_default_tags();

COMMIT;
```

(A CHECK cannot hold a subquery, hence the IMMUTABLE helper for the no-duplicates rule; the down migration drops it
after the constraint.)

- [ ] **Step 4: Write `db/migrations/0096_prompt_tags.down.sql`**

```sql
BEGIN;
SET search_path = ka, public;

DROP TRIGGER documentation_prompt_default_tags ON documentation_prompts;
DROP FUNCTION documentation_prompt_default_tags();
DROP INDEX documentation_prompts_tags_gin;
ALTER TABLE documentation_prompts
    DROP CONSTRAINT documentation_prompts_tags,
    DROP COLUMN tags,
    ALTER COLUMN input_kind DROP DEFAULT;
DROP FUNCTION documentation_prompt_tags_unique(text[]);
COMMENT ON COLUMN documentation_prompts.input_kind IS NULL;

COMMIT;
```

- [ ] **Step 5: `db/tests/run.sh`**

1. Right after the two `run_migrate postgres up` lines, before the existing first `run_migrate postgres down 1` (the
   0095 check), insert:

```sh
run_migrate postgres down 1 >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
tags_removed=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'ka'
        AND table_name = 'documentation_prompts' AND column_name = 'tags')")
[ "$tags_removed" = t ] || { echo 'FAIL: 0096 down left prompt tags' >&2; exit 1; }
```

   The chain must still end at version 83 (`rolled_back_version`): with one more migration and one more `down 1`, it
   does.
2. After the `PASS: inventory links replace the stored system graph.` line add:

```sh
tags=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'ka'
        AND table_name = 'documentation_prompts' AND column_name = 'tags')")
[ "$tags" = t ] || { echo 'FAIL: 0096 prompt tags missing' >&2; exit 1; }
printf '%s\n' 'PASS: prompts carry tags.'
```

- [ ] **Step 6: `docs/migrations.md`** — append:

```markdown
## 0096_prompt_tags

Prompts get `tags` (lowercase letters, digits and `-`, up to 40 characters, at most 10, no duplicates), backfilled as
`documentation` plus the old `input_kind`. `input_kind` is retired but kept with `DEFAULT 'mixed'`, because the
per-workspace seed functions still insert it; a BEFORE INSERT trigger tags an untagged insert the same way, so seeded
prompts of new workspaces are tagged too. The down migration drops the tags, the trigger and the default.
```

- [ ] **Step 7: Run the tests**

Run: `cd services/admin-api && uv run pytest -q tests/test_prompt_tags_migration.py tests/test_documentation_fixture.py`
Expected: PASS. (`test_documentation_fixture.py` still reads `input_kind` through the API until Task 2 — it must still
pass here because the API is unchanged in this task.) Run `bash db/tests/run.sh` from the worktree root if Docker is
available; it must print `PASS: prompts carry tags.`

- [ ] **Step 8: Commit**

```bash
git add db/migrations/0096_prompt_tags.up.sql db/migrations/0096_prompt_tags.down.sql db/tests/run.sh docs/migrations.md services/admin-api/tests/test_prompt_tags_migration.py
git commit -m "feat(db): prompts carry tags; input_kind retired but kept for the seed functions (0096)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Prompts API with tags

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/documentation_prompts.py`
- Modify tests: `services/admin-api/tests/test_documents.py` (`PROMPT`), `services/admin-api/tests/test_documentation_fixture.py`
- Test: `services/admin-api/tests/test_prompt_tags_api.py` (new)

**Interfaces:**
- Consumes: column `tags` (Task 1).
- Produces: `PromptInput{name, description, tags: list[str], enabled, content, revision?}` (no `input_kind`, extra
  fields refused); list rows and detail carry `tags` and no `input_kind`; `GET /api/documentation/prompts?tag=<tag>`;
  `GET /api/documentation/prompts/tags` → `{"tags": [{"tag": str, "count": int}]}` ordered by tag.

- [ ] **Step 1: Write the failing tests**

Create `services/admin-api/tests/test_prompt_tags_api.py`:

```python
"""Administration → Prompts: tags on prompts, the tag filter and the tags in use."""
import pytest

from test_api import _client
from test_repositories import HEADERS

ENDPOINT = '/api/documentation/prompts'


@pytest.fixture
def client(store, tmp_path):
    return _client(tmp_path, repository_store=store)


def make(client, name, tags, content='Look around.'):
    response = client.post(ENDPOINT, headers=HEADERS, json={'name': name, 'tags': tags, 'content': content})
    assert response.status_code == 201, response.text
    return response.json()['id']


def message(response) -> tuple:
    return response.status_code, response.json()['error']['message']


def test_tags_are_normalised_saved_and_returned(client):
    prompt = make(client, 'Discovery', [' Server-Discovery ', 'server-discovery', 'SSH'])
    detail = client.get(f'{ENDPOINT}/{prompt}', headers=HEADERS).json()
    assert detail['tags'] == ['server-discovery', 'ssh'] and 'input_kind' not in detail
    listed = next(row for row in client.get(ENDPOINT, headers=HEADERS).json()['prompts'] if row['id'] == prompt)
    assert listed['tags'] == ['server-discovery', 'ssh'] and 'input_kind' not in listed


def test_changing_only_tags_adds_no_version(client):
    prompt = make(client, 'Runbook', ['service'], content='Write a runbook.')
    saved = client.put(f'{ENDPOINT}/{prompt}', headers=HEADERS,
                       json={'name': 'Runbook', 'tags': ['service', 'runbook'], 'content': 'Write a runbook.', 'revision': 1})
    assert saved.status_code == 200, saved.text
    detail = client.get(f'{ENDPOINT}/{prompt}', headers=HEADERS).json()
    assert detail['tags'] == ['service', 'runbook'] and len(detail['versions']) == 1


def test_bad_tags_and_input_kind_are_refused(client):
    assert message(client.post(ENDPOINT, headers=HEADERS, json={'name': 'X', 'tags': ['has space'], 'content': 'c'})) == (
        400, 'Tags use lowercase letters, digits and -, up to 40 characters.')
    assert message(client.post(ENDPOINT, headers=HEADERS, json={'name': 'X', 'tags': [f't{i}' for i in range(11)], 'content': 'c'})) == (
        400, 'Use at most 10 tags.')
    assert client.post(ENDPOINT, headers=HEADERS, json={'name': 'X', 'input_kind': 'code', 'content': 'c'}).status_code == 400


def test_filter_by_tag_and_tags_in_use(client):
    discovery = make(client, 'Discovery', ['server-discovery', 'ssh'])
    make(client, 'Another', ['ssh'])
    filtered = client.get(ENDPOINT, headers=HEADERS, params={'tag': 'server-discovery'}).json()['prompts']
    assert [row['id'] for row in filtered] == [discovery]
    tags = {row['tag']: row['count'] for row in client.get(f'{ENDPOINT}/tags', headers=HEADERS).json()['tags']}
    assert tags['ssh'] == 2 and tags['server-discovery'] == 1
    assert tags['documentation'] >= 1  # the seeded prompts of the test workspace
    ordered = [row['tag'] for row in client.get(f'{ENDPOINT}/tags', headers=HEADERS).json()['tags']]
    assert ordered == sorted(ordered)


def test_a_prompt_without_tags_gets_the_default(client):
    prompt = make(client, 'Plain', [])
    assert client.get(f'{ENDPOINT}/{prompt}', headers=HEADERS).json()['tags'] == ['documentation', 'mixed']
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd services/admin-api && uv run pytest -q tests/test_prompt_tags_api.py`
Expected: FAIL (`input_kind` required; `tags` unknown; `/tags` answers 400 as a bad UUID).

- [ ] **Step 3: Implement in `documentation_prompts.py`**

1. Imports: drop `Literal` if unused; add `import re`.
2. Replace `PromptInput`:

```python
TAG = re.compile(r'^[a-z0-9][a-z0-9-]{0,39}$')
MAX_TAGS = 10
BAD_TAG = 'Tags use lowercase letters, digits and -, up to 40 characters.'
TOO_MANY_TAGS = f'Use at most {MAX_TAGS} tags.'


def clean_tags(tags: list[str]) -> list[str]:
    """Trimmed, lower-cased, duplicates merged in first-seen order; 400 with a plain message when one is invalid."""
    cleaned: list[str] = []
    for tag in tags:
        tag = tag.strip().lower()
        if not TAG.match(tag):
            raise HTTPException(400, BAD_TAG)
        if tag not in cleaned:
            cleaned.append(tag)
    if len(cleaned) > MAX_TAGS:
        raise HTTPException(400, TOO_MANY_TAGS)
    return cleaned


class PromptInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default='', max_length=4000)
    tags: list[str] = Field(default_factory=list, max_length=100)
    enabled: bool = Field(default=True, strict=True)
    content: str = Field(min_length=1, max_length=100000)
    revision: int | None = Field(default=None, ge=1, strict=True)
    (keep the existing valid_text validator unchanged)
```

3. A column list without `input_kind`, used by the list and the detail:

```python
PROMPT_COLUMNS = ('id,project_id,name,description,tags,enabled,active_version_id,next_version,revision,'
                  'created_at,updated_at')
```

   These are all of the table's columns (migration 0040 plus `tags`) except `input_kind`.
4. `list_prompts(tag: str | None = Query(None, max_length=40))`: select `p.<each PROMPT_COLUMNS column>` (prefix each
   with `p.`), keep `active_version` and `version_count`, add `AND (%s::text IS NULL OR %s = ANY(p.tags))`.
5. Add, **before** `@router.get('/{prompt_id}')` (otherwise `tags` is parsed as a UUID):

```python
    @router.get('/tags')
    def tags_in_use() -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app_name)
            rows = conn.execute('''SELECT tag, count(*) AS count FROM ka.documentation_prompts, unnest(tags) AS tag
                WHERE project_id=%s GROUP BY tag ORDER BY tag''', (project,)).fetchall()
        return {'tags': rows}
```

6. `get_prompt`: `SELECT {PROMPT_COLUMNS} FROM …`.
7. `save`: `tags = clean_tags(value.tags)` at the top (before opening the connection); INSERT
   `(project_id,name,description,tags,enabled) VALUES (%s,%s,%s,%s,%s) RETURNING {PROMPT_COLUMNS}` with `tags` — an
   empty list is filled in by the 0096 trigger (`documentation, mixed`); UPDATE sets `tags=%s` instead of
   `input_kind=%s` (an update to `[]` stores `[]`: the trigger runs on INSERT only). The version logic is unchanged (a version is added only
   when `content` differs), which is what makes tag-only edits version-free.
8. Audit details of `documentation_prompt.saved` gain `'tags': tags`.

- [ ] **Step 4: Update existing tests**

- `tests/test_documents.py`: `PROMPT` drops `'input_kind':'code'` and gains `'tags':['code']`.
- `tests/test_documentation_fixture.py`: the assertion `fixture['input_kind'] == 'code'` becomes
  `fixture['tags'] == ['documentation', 'code']`; any request body built from a fetched prompt must not send
  `input_kind` (send `tags` instead).
- `grep -rn "input_kind" services/admin-api/src` prints only migrations-free mentions — nothing in `src`.

- [ ] **Step 5: Run the tests**

Run: `cd services/admin-api && uv run pytest -q tests/test_prompt_tags_api.py tests/test_prompt_tags_migration.py tests/test_documents.py tests/test_documentation_fixture.py tests/test_generations.py`
Expected: PASS. Then the full admin-api suite once — only the known failures.

- [ ] **Step 6: Commit**

```bash
git add services/admin-api
git commit -m "feat(api): prompts are filtered and organised by tags; input_kind no longer used

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Administration → Prompts page, tags input, Generate documents filter, docs

**Files:**
- Create: `services/admin-ui/app/components/TagsInput.tsx`, `services/admin-ui/app/routes/promptLibrary.tsx`,
  `services/admin-ui/app/routes/promptLibraryEditor.tsx`
- Modify: `services/admin-ui/app/routes.ts`, `routes/documentationPrompts.tsx`, `routes/documentationPromptNew.tsx`,
  `routes/documentationPromptDetail.tsx` (become redirects), `app/lib/documents.ts`,
  `app/components/DocumentationPromptsView.tsx`, `app/components/AdminLayout.tsx`,
  `app/components/DocumentGenerationForm.tsx`, `docs/user-guide.md`
- Test: `app/components/__tests__/tagsInput.test.tsx` (new), `app/components/__tests__/promptLibrary.test.tsx` (new);
  update `adminLayout.test.tsx`, `documents.test.tsx`, `documentGenerationForm.test.tsx` fixtures (`input_kind` →
  `tags`, links → `/prompts…`)

**Interfaces:**
- Consumes: API from Task 2 (`tags` on rows, `?tag=`, `GET /api/documentation/prompts/tags`).
- Produces: `TagsInput({ id, value, onChange, suggestions }: { id: string; value: string[]; onChange: (tags: string[]) =>
  void; suggestions: string[] })`; `getPromptTags(signal?) => Promise<{ tags: { tag: string; count: number }[] }>`;
  `getDocumentationPrompts(signal?, tag?)`.

- [ ] **Step 1: Write the failing tests**

Create `app/components/__tests__/tagsInput.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import TagsInput from "../TagsInput";

function Harness({ initial = [] as string[] }) {
  const [tags, setTags] = useState(initial);
  return <><TagsInput id="tags" value={tags} onChange={setTags} suggestions={["server-discovery", "ssh"]} /><output data-testid="tags">{tags.join("|")}</output></>;
}

test("adds tags with Enter or comma, normalises them and removes them with ×", async () => {
  const user = userEvent.setup();
  render(<Harness />);
  const input = screen.getByRole("combobox", { name: "Tags" });
  await user.type(input, " Server-Discovery {Enter}");
  await user.type(input, "ssh,");
  await user.type(input, "ssh{Enter}");  // duplicate ignored
  expect(screen.getByTestId("tags").textContent).toBe("server-discovery|ssh");
  await user.click(screen.getByRole("button", { name: "Remove tag ssh" }));
  expect(screen.getByTestId("tags").textContent).toBe("server-discovery");
});

test("explains an invalid tag and keeps it out", async () => {
  const user = userEvent.setup();
  render(<Harness />);
  await user.type(screen.getByRole("combobox", { name: "Tags" }), "has space{Enter}");
  expect(screen.getByText("Tags use lowercase letters, digits and -, up to 40 characters.")).toBeInTheDocument();
  expect(screen.getByTestId("tags").textContent).toBe("");
});

test("stops at 10 tags and suggests only tags not yet chosen", () => {
  const { container } = render(<Harness initial={["ssh", ...Array.from({ length: 9 }, (_, i) => `t${i}`)]} />);
  expect(screen.getByRole("combobox", { name: "Tags" })).toBeDisabled();
  expect(screen.getByText("Use at most 10 tags.")).toBeInTheDocument();
  expect([...container.querySelectorAll("datalist option")].map(option => option.getAttribute("value"))).toEqual(["server-discovery"]);
});
```

Create `app/components/__tests__/promptLibrary.test.tsx`:

```tsx
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import DocumentationPromptsView from "../DocumentationPromptsView";
import DocumentationPromptsRedirect from "../../routes/documentationPrompts";
import DocumentationPromptDetailRedirect from "../../routes/documentationPromptDetail";

const prompts = [
  { id: "p1", name: "Discovery", description: "Look around", tags: ["server-discovery", "ssh"], enabled: true, active_version_id: "v1", active_version: 1, version_count: 1, revision: 1 },
  { id: "p2", name: "Runbook", description: "", tags: ["documentation", "service"], enabled: true, active_version_id: "v2", active_version: 1, version_count: 1, revision: 1 },
];
function api(routes: Record<string, unknown>) {
  const fetch = vi.fn(async (url: string) => {
    if (!(url in routes)) throw new Error(`unexpected ${url}`);
    return new Response(JSON.stringify(routes[url]), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
function Where() { return <output data-testid="where">{useLocation().pathname}</output>; }
afterEach(() => vi.unstubAllGlobals());

test("lists prompts with their tags and filters by tag", async () => {
  const user = userEvent.setup();
  api({ "/api/documentation/prompts": { prompts }, "/api/documentation/prompts/tags": { tags: [{ tag: "documentation", count: 1 }, { tag: "server-discovery", count: 1 }, { tag: "service", count: 1 }, { tag: "ssh", count: 1 }] },
        "/api/documentation/prompts?tag=ssh": { prompts: [prompts[0]] } });
  render(<MemoryRouter initialEntries={["/prompts"]}><Routes><Route path="/prompts" element={<DocumentationPromptsView />} /></Routes></MemoryRouter>);
  const row = (await screen.findByRole("link", { name: "Discovery" })).closest("tr")!;
  expect(within(row).getByText("server-discovery")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Discovery" })).toHaveAttribute("href", "/prompts/p1");
  await user.click(screen.getByRole("combobox", { name: "Filter by tag" }));
  await user.click(screen.getByRole("option", { name: "ssh" }));
  await waitFor(() => expect(screen.queryByRole("link", { name: "Runbook" })).toBeNull());
});

test("old addresses redirect to the Prompts page", async () => {
  render(<MemoryRouter initialEntries={["/documentation/prompts/p1"]}><Routes>
    <Route path="/documentation/prompts" element={<DocumentationPromptsRedirect />} />
    <Route path="/documentation/prompts/:promptId" element={<DocumentationPromptDetailRedirect />} />
    <Route path="*" element={<Where />} /></Routes></MemoryRouter>);
  await waitFor(() => expect(screen.getByTestId("where").textContent).toBe("/prompts/p1"));
});
```

In `documentGenerationForm.test.tsx` change the three prompt fixtures from `input_kind` to tags — p1
`tags: ['code']`, p2 `tags: ['service', 'runbook']`, p3 `tags: ['mixed', 'draft']` — and add:

```tsx
test('the prompt picker shows tags and filters by tag; tags of unusable prompts are not offered', async () => {
  setup();
  fireEvent.click(await screen.findByLabelText('Prompt'));
  expect((await screen.findAllByRole('option')).map(option => option.textContent)).toEqual(
    ['Write my own', 'API reference · code', 'Runbook · service, runbook']);
  fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' });
  fireEvent.click(await screen.findByLabelText('Prompt tag'));
  // p3 is disabled, so its tag "draft" (and "mixed") is not offered
  expect((await screen.findAllByRole('option')).map(option => option.textContent)).toEqual(['All tags', 'code', 'runbook', 'service']);
  fireEvent.click(await screen.findByRole('option', { name: 'runbook' }));
  fireEvent.click(await screen.findByLabelText('Prompt'));
  expect((await screen.findAllByRole('option')).map(option => option.textContent)).toEqual(['Write my own', 'Runbook · service, runbook']);
});
```

(If closing the open select with Escape does not work in this file's setup, use the same way other tests in the file
close or re-open a select; keep the three assertions.)

- [ ] **Step 2: Run them to see them fail**

Run: `cd services/admin-ui && npx vitest run app/components/__tests__/tagsInput.test.tsx app/components/__tests__/promptLibrary.test.tsx app/components/__tests__/documentGenerationForm.test.tsx`
Expected: FAIL (`TagsInput` missing, links still `/documentation/prompts…`, no tag filter).

- [ ] **Step 3: `app/lib/documents.ts`**

```ts
export const TAG_PATTERN = /^[a-z0-9][a-z0-9-]{0,39}$/;
export const MAX_TAGS = 10;
export interface DocumentationPromptInput { name: string; description: string; tags: string[]; enabled: boolean; content: string; revision?: number }
// DocumentationPrompt and DocumentationPromptDetail keep their shape, now carrying tags (they extend the input type)
export const getDocumentationPrompts = (signal?: AbortSignal, tag?: string) =>
  request<{ prompts: DocumentationPrompt[] }>(`/api/documentation/prompts${tag ? `?${new URLSearchParams({ tag })}` : ''}`, { signal });
export const getPromptTags = (signal?: AbortSignal) => request<{ tags: { tag: string; count: number }[] }>('/api/documentation/prompts/tags', { signal });
```

- [ ] **Step 4: `app/components/TagsInput.tsx`**

```tsx
import { useState, type KeyboardEvent } from "react";
import { X } from "lucide-react";
import { MAX_TAGS, TAG_PATTERN } from "~/lib/documents";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { FieldDescription, FieldError } from "~/components/ui/field";
import { Input } from "~/components/ui/input";

const BAD_TAG = "Tags use lowercase letters, digits and -, up to 40 characters.";

/** Tags typed one at a time: Enter or comma adds the typed tag (trimmed, lower-cased); × removes one. */
export default function TagsInput({ id, value, onChange, suggestions }: { id: string; value: string[]; onChange: (tags: string[]) => void; suggestions: string[] }) {
  const [text, setText] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const full = value.length >= MAX_TAGS;
  function add() {
    const tag = text.trim().toLowerCase();
    if (!tag) return;
    if (!TAG_PATTERN.test(tag)) { setProblem(BAD_TAG); return; }
    setProblem(null); setText("");
    if (!value.includes(tag)) onChange([...value, tag]);
  }
  function key(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter" || event.key === ",") { event.preventDefault(); add(); }
  }
  return <div className="flex flex-col gap-2">
    {value.length > 0 && <ul className="flex flex-wrap gap-1">{value.map(tag => <li key={tag}><Badge variant="secondary" className="gap-1">{tag}
      <Button type="button" variant="ghost" size="icon" className="size-4" aria-label={`Remove tag ${tag}`} onClick={() => onChange(value.filter(item => item !== tag))}><X /></Button></Badge></li>)}</ul>}
    <Input id={id} role="combobox" aria-label="Tags" list={`${id}-suggestions`} value={text} disabled={full} placeholder="Type a tag and press Enter"
      aria-invalid={problem !== null} onChange={event => { setText(event.target.value); setProblem(null); }} onKeyDown={key} onBlur={add} />
    <datalist id={`${id}-suggestions`}>{suggestions.filter(tag => !value.includes(tag)).map(tag => <option key={tag} value={tag} />)}</datalist>
    {problem ? <FieldError>{problem}</FieldError> : full ? <FieldDescription>Use at most {MAX_TAGS} tags.</FieldDescription>
      : <FieldDescription>Lowercase letters, digits and -. Features find prompts by tag, for example server-discovery.</FieldDescription>}
  </div>;
}
```

(`onBlur={add}` adds a typed tag when the field loses focus, so a tag typed without Enter is not lost on Save. If it
makes the invalid-tag test flaky, keep it and assert after blur.)

- [ ] **Step 5: `DocumentationPromptsView.tsx`**

- List: load `getPromptTags` alongside; a `Select` (aria-label "Filter by tag", options "All tags" + each tag; "All
  tags" uses a sentinel value such as `"__all__"`, because a Radix `SelectItem` cannot have `value=""`) above the
  table; choosing a tag reloads with `getDocumentationPrompts(signal, tag)`. Replace the "Input" column with "Tags":
  each tag a `Badge variant="outline"`. Links become `/prompts/${prompt.id}` and `/prompts/new`. Intro text:
  "Reusable instructions for agents, organised by tags. New prompt versions remain drafts until activated." Empty
  state: "No prompts yet" / "Create prompts for documentation, server discovery and other agent work, and tag them so
  features can find them."
- Editor: `initial` uses `tags: prompt?.tags ?? []` (no `input_kind`); replace the "Input type" select with
  `<Field><FieldLabel htmlFor="documentation-prompt-tags">Tags</FieldLabel><TagsInput id="documentation-prompt-tags" value={value.tags} onChange={tags => setValue({ ...value, tags })} suggestions={tagsInUse} /></Field>`
  where `tagsInUse` comes from `getPromptTags` (load it in the editor view; on error use `[]`). The switch label
  becomes "Enabled". Back link and post-delete navigation go to `/prompts`; `onSaved` navigates to `/prompts/${id}`.

- [ ] **Step 6: Routes and layout**

- `routes.ts`: add `route("prompts", "./routes/promptLibrary.tsx")`, `route("prompts/new", "./routes/promptLibraryEditor.tsx")`,
  `route("prompts/:promptId", "./routes/promptLibraryEditor.tsx")`; keep the three `documentation/prompts…` routes.
- `routes/promptLibrary.tsx` = the current `documentationPrompts.tsx` body; `routes/promptLibraryEditor.tsx` = the
  current `documentationPromptDetail.tsx` body.
- The three old route files become redirects:

```tsx
// routes/documentationPrompts.tsx
import { Navigate } from "react-router";
export default function Page() { return <Navigate to="/prompts" replace />; }
// routes/documentationPromptNew.tsx
import { Navigate } from "react-router";
export default function Page() { return <Navigate to="/prompts/new" replace />; }
// routes/documentationPromptDetail.tsx
import { Navigate, useParams } from "react-router";
export default function Page() { const { promptId } = useParams(); return <Navigate to={`/prompts/${promptId ?? ""}`} replace />; }
```

- `AdminLayout.tsx`: `const promptsPage = pathname === "/prompts" || pathname.startsWith("/prompts/");` replaces
  `docsPrompts`; remove the Prompts item from the Docs group and add
  `<SidebarMenuItem><SidebarMenuButton asChild isActive={promptsPage}><Link to="/prompts"><BookOpen /><span>Prompts</span></Link></SidebarMenuButton></SidebarMenuItem>`
  to the Administration group after Skills. Title "Prompts"; description "Reusable instructions for agents, organised by
  tags." (`docsPage` no longer needs the prompts case.)
- `adminLayout.test.tsx`: the Prompts link is now in the Administration navigation with href `/prompts` (update the
  existing assertion, keep its style).

- [ ] **Step 7: `DocumentGenerationForm.tsx`**

- Add state `promptTag` (default `""` = all). Above the Prompt select, a `Select` labelled "Prompt tag" (id
  `generate-prompt-tag`) with "All tags" and every tag carried by `data.prompts` — which already holds only enabled
  prompts with an active version — sorted and unique. (A Radix `SelectItem` cannot have `value=""`: use a sentinel such
  as `"__all__"` for "All tags" and map it to no filter.)
- The Prompt select lists `data.prompts.filter(item => !promptTag || item.tags.includes(promptTag))`; each option reads
  `` `${item.name}${item.tags.length ? ` · ${item.tags.join(", ")}` : ""}` ``. "Write my own" is always there. If the
  chosen prompt is filtered out, keep the chosen prompt text (the select shows the choice it still holds) — do not
  clear the user's prompt text.
- Update the test fixtures from `input_kind` to `tags` as given in Step 1.

- [ ] **Step 8: `docs/user-guide.md`**

- §4.1 table: Administration row becomes `Storage · Prompts · Skills · MCP servers · Agent runs · Schedules · Events ·
  Notifications`; Docs row becomes `Documents · Generate documents · Folders`.
- §4.12 Prompt bullet: "**Prompt**: **Write my own**, or a saved prompt from Administration → Prompts; **Prompt tag**
  narrows the list. **Prompt text** is the first message; editing a saved prompt changes only this run."
- §4.13: retitle "### 4.13 Documents"; move the Prompts paragraph into a new section placed right after §4.14 Storage
  as "### 4.14a Prompts" — or, if the numbering style forbids letters, as the last subsection of chapter 4 ("### 4.21
  Prompts"). Its text:

```markdown
**Administration → Prompts** (`/prompts`) is the library of reusable instructions for agents. Each prompt has tags —
lowercase letters, digits and `-`, at most 10 — that features use to find it: Generate documents can filter by tag,
and the server discovery sheet will offer prompts tagged `server-discovery`. Type a tag and press Enter to add it;
existing tags are suggested. Saving changed text creates a draft version; **Activate vN** makes it the active one;
inactive versions can be deleted. Changing only the name, description, tags or Enabled does not create a version.
Prompts that came with the application are tagged `documentation` plus what they document (`code`, `server`,
`service`, `mixed`). Old `/documentation/prompts` addresses open the same page.
```

- §5 workflow step "4. **Docs → Prompts**" becomes "4. **Administration → Prompts**".

- [ ] **Step 9: Run the UI tests and type check**

Run: `cd services/admin-ui && npx vitest run && npx tsc --noEmit`
Expected: all PASS, no type errors. `grep -rn "input_kind\|documentation/prompts" services/admin-ui/app` prints only the
three redirect route files, `routes.ts` and the redirect test.

- [ ] **Step 10: Commit**

```bash
git add services/admin-ui docs/user-guide.md
git commit -m "feat(admin-ui): Prompts move to Administration with tags, a tag filter and redirects; Generate documents filters by tag

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```
