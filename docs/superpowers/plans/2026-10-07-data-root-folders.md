# One Data Folder With Named Subfolders Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The application keeps all its files in one host folder, `DATA_ROOT`. `.env`
names that folder and, relative to it, the five folders the sources write to
(`CODE_FOLDER`, `CODE_INDEX_FOLDER`, `DOCS_FOLDER`, `GENERATED_DOCS_FOLDER`,
`JIRA_FOLDER`). Compose mounts `DATA_ROOT` once, read-only, at `/work/data`, with a
writable nested bind mount only where a service writes. RAG reads through that one
root, so **Add folder** can pick any folder in `DATA_ROOT`; it writes only into
`JIRA_FOLDER`. The `rag-data` area and the absolute per-folder overrides are gone.

**Architecture:**
- **`make setup` (`scripts/setup.py`)** validates the five relative folder variables
  (no leading `/`, no `..`, no hidden or empty segments, no overlap), refuses the old
  absolute overrides naming their replacement, moves `DATA_ROOT/rag-data/jira` to
  `DATA_ROOT/${JIRA_FOLDER}` once, creates the folders and warns about any other
  top-level entry of `DATA_ROOT` (it never moves or deletes those).
- **Compose (`deploy/compose.yaml`)** gets YAML anchors: `&data-root` (the read-only
  base bind), one writable bind per folder (`&code-rw`, `&code-index-rw`,
  `&generated-docs-rw`, `&jira-rw`) and `&data-env` (the in-container variables
  computed from the folder variables). Every service that used a data folder mounts
  `*data-root` plus its writable folders.
- **RAG (`services/rag`)**: `rag.collections.Areas` becomes `DataRoot` (one container
  root, one host root, one writable folder). `source_path` accepts any visible path;
  `GET folders` without `path` lists the top level of `DATA_ROOT`; **Add folder**
  never creates folders; `PUT file` and collection-less `GET file` work only under
  `JIRA_FOLDER`. `Workspace.areas` is renamed `Workspace.data_root`.
- **Migration 0067** relaxes the `source_path` check, rewrites `rag-data/jira…` to
  `jira…` in `rag_collection_paths.source_path`, `rag_files.path` and
  `rag_file_log.path`, and rewrites saved agent versions' container paths to the
  `/work/data` mount. The down migration rewrites back.
- **admin-api**: a new `knowledge_admin/data_root.py` (container base, host paths,
  the Jira folder) replaces `collections.area_root`; the shell worker allows reads
  under `DATA_ROOT_DIR`; the storage overview lists `DATA_ROOT` and the five folders;
  the Jira export writes `<JIRA_FOLDER>/<KEY>.md`, and `GET /api/jira/tickets`
  returns `export_folder`.
- **admin-ui**: the storage overview shows `DATA_ROOT` and the five folders with
  their `.env` variables; RAG Settings shows the data folder; the folder picker
  starts at the `DATA_ROOT` top level and no longer promises to create folders; the
  Jira page names the configured folder. No UI text mentions `rag-data`.

**Tech Stack:**
- Python 3.12 standard library and `unittest` (`scripts/setup.py`, `tests/`);
- Docker Compose v2 (`docker compose config`), PyYAML in the docs-wiki deploy tests;
- PostgreSQL 17 with pgvector, golang-migrate SQL migrations;
- FastAPI and psycopg3 (`services/rag`, `services/admin-api`), pytest against a real
  PostgreSQL container (`knowledge-agent-v3-postgres:17.10-vector0.8.6`);
- React Router 7 with shadcn/ui and Vitest (`services/admin-ui`).

**Spec:** `docs/superpowers/specs/2026-10-07-data-root-folders-design.md` (binding).

## Global Constraints

- `.env` variables: `DATA_ROOT` (required, absolute host path); `CODE_FOLDER` default `code`; `CODE_INDEX_FOLDER` default `code-index`; `DOCS_FOLDER` default `docs`; `GENERATED_DOCS_FOLDER` default `generated-documents`; `JIRA_FOLDER` default `jira`.
- A folder variable is a relative path whose every `/`-separated segment matches `[A-Za-z0-9_-][A-Za-z0-9._-]*` (so: no leading `/`, no `..`, no hidden or empty segments). Two folder variables overlap when `a == b` or one starts with the other plus `/`; overlap is refused.
- Removed variables and their replacements: `CODE_PATH` → `CODE_FOLDER`, `CODE_INDEX_PATH` → `CODE_INDEX_FOLDER`, `RG_DATA` → `JIRA_FOLDER`, `GENERATED_DOCS_PATH` → `GENERATED_DOCS_FOLDER`, `DOCS_PATH` → `DOCS_FOLDER`, `RAG_SOURCE_ROOT` → none. `make setup` stops when any is set.
- Setup error texts:
  - invalid folder: `<KEY> must be a folder inside DATA_ROOT such as <default>: a relative path without a leading /, .., hidden or empty segments; got '<value>'.`
  - overlap: `<KEY_A> (<a>) and <KEY_B> (<b>) overlap; choose separate folders inside DATA_ROOT.`
  - old override inside `DATA_ROOT`: `<KEY> is no longer used. Replace it in .env with <NEW>=<relative path>.`
  - old override outside `DATA_ROOT`: `<KEY> is no longer used. Move <value> into <DATA_ROOT>, then replace <KEY> in .env with <NEW>=<folder inside DATA_ROOT>.`
  - `RG_DATA`: `RG_DATA is no longer used; remove it from .env. Jira exports now go to JIRA_FOLDER inside DATA_ROOT (default jira); make setup moves <DATA_ROOT>/rag-data/jira there.`
  - `RAG_SOURCE_ROOT`: `RAG_SOURCE_ROOT is no longer used; remove it from .env.`
- Setup move: `DATA_ROOT/rag-data/jira` → `DATA_ROOT/${JIRA_FOLDER}` only when the source is a real directory and the target does not exist; prints `Moved <old> to <new>.`; then removes `DATA_ROOT/rag-data` only if it is now empty and prints `Removed the empty folder <DATA_ROOT>/rag-data.`
- Setup warning (never stops, never moves): `Warning: <DATA_ROOT> holds entries that are not configured folders: <names, sorted, comma-separated>. RAG can see every folder in DATA_ROOT; move them out, for example to a sibling DATA_ARCHIVE folder. Nothing was moved.` An entry is configured when its name is the first segment of a folder variable.
- Container base: `DATA_ROOT` bind-mounted once at `/work/data`, `read_only: true`, in `rag`, `rag-worker`, `docs-wiki`, `admin-api`, `repository-worker`, `documentation-worker`, `shell-worker`.
- Writable nested binds (source `${DATA_ROOT}/${X_FOLDER:-default}`, target `/work/data/${X_FOLDER:-default}`): `rag` → `JIRA_FOLDER`; `admin-api` → `CODE_FOLDER`, `CODE_INDEX_FOLDER`, `GENERATED_DOCS_FOLDER`; `repository-worker` → `CODE_FOLDER`, `CODE_INDEX_FOLDER`; `documentation-worker` → `CODE_INDEX_FOLDER`, `GENERATED_DOCS_FOLDER`; none for `rag-worker`, `docs-wiki`, `shell-worker`.
- In-container variables (anchor `&data-env`, in `rag`, `rag-worker`, `admin-api`, `repository-worker`, `documentation-worker`): `DATA_ROOT_DIR=/work/data`, `DATA_ROOT=${DATA_ROOT}`, `CODE_ROOT=/work/data/${CODE_FOLDER:-code}`, `CODE_INDEX_ROOT=/work/data/${CODE_INDEX_FOLDER:-code-index}`, `BOOTSTRAP_DOCS_ROOT=/work/data/${DOCS_FOLDER:-docs}`, `DOCUMENT_OUTPUT_ROOT=/work/data/${GENERATED_DOCS_FOLDER:-generated-documents}`, `JIRA_EXPORT_ROOT=/work/data/${JIRA_FOLDER:-jira}`.
- RAG `source_path`: at most 500 characters, `^[A-Za-z0-9_-][A-Za-z0-9._-]*(/[A-Za-z0-9_-][A-Za-z0-9._-]*)*$`; a folder path containing a `node_modules` or `.git` segment is refused with 400 `Folder <path> is skipped by RAG`.
- RAG's writable folder is `JIRA_EXPORT_ROOT` relative to `DATA_ROOT_DIR` (`DataRoot.jira_folder`).
- `GET folders` without `path` → `{path: null, truncated, folders: [{name, path: name}]}` for the visible top-level folders of `DATA_ROOT`, sorted, at most 1000; links, hidden folders, `.git`, `node_modules` and non-segment names are not listed.
- `POST collections/{id}/paths` with a missing folder → 409 `Folder does not exist`; RAG never creates folders there.
- `PUT file` outside `<JIRA_FOLDER>/` → 403 `Only files inside <JIRA_FOLDER> can be written; other folders are read-only`; a hidden or skipped segment below it → 400. A missing Jira folder on write → 409 `Folder <JIRA_FOLDER> does not exist; run make setup to create it`.
- `GET file` without `collection` serves only visible files under `<JIRA_FOLDER>/`; anything else → 404 `Document not found`.
- Host paths: `DATA_ROOT` + `/` + `source_path` (`None` when `DATA_ROOT` is not set in the container).
- RAG `GET settings` / `PUT settings` response: `{revision, settings, embedding_model_id, data_root: {container_root, host_root, jira_folder}}` (no `areas`).
- Jira export path: `<jira_folder()>/<KEY>.md`; `GET /api/jira/tickets` and `POST /api/jira/tickets/fetch` return `{connection, tickets, export_folder}`.
- Storage overview `locations` ids in order: `data`, `code`, `code_index`, `jira`, `documents`, `bootstrap`; each `{id, container_path, host_path, variable, status}` with `variable` = `DATA_ROOT`, `CODE_FOLDER`, `CODE_INDEX_FOLDER`, `JIRA_FOLDER`, `GENERATED_DOCS_FOLDER`, `DOCS_FOLDER`.
- Migration number 0067 (`0067_data_root_folders`). `db/tests/run.sh` pins the rolled-back version to `66`.
- Commit trailer: `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Python stays compatible with 3.12.

## Review Focus

1. **Writes escaping the Jira folder.** Examples: `PUT file` for `code/x.md`,
   `jira2/x.md` (a prefix look-alike), `jira` itself, `jira/.env`,
   `jira/node_modules/a.md`; a service other than `rag` getting the Jira folder
   writable; a writer service losing its folder. Each write outside
   `<JIRA_FOLDER>/` is 403, hidden ones 400, and only the listed services get
   writable binds. Pinned in:
   - Task 4: `test_jira_writer_writes_only_inside_the_jira_folder`;
   - Task 2: `test_data_root_is_mounted_once_read_only_with_writable_folders_only_for_writers`.
2. **Reads escaping `DATA_ROOT` now that any top-level folder is allowed.**
   Examples: a top-level symbolic link to `/etc`, `..`, `.local-archive`, `.git`,
   a trailing `/`, a missing folder that Add folder used to create. Each gives its
   exact error, creates nothing and serves nothing. Pinned in Task 4:
   - `test_reads_go_through_the_one_data_root_and_never_follow_links`;
   - `test_add_folder_rejects_overlap_missing_and_bad_paths_and_changes_nothing`;
   - `test_folder_browser_lists_the_data_root_and_rejects_bad_paths`.
3. **Folder variables that escape, overlap or are silently ignored.** Examples:
   `JIRA_FOLDER=/srv/jira`, `CODE_FOLDER=../code`, `DOCS_FOLDER=.docs`,
   `CODE_INDEX_FOLDER=code/index`, two variables naming the same folder, and an old
   `CODE_PATH` left in `.env`. Setup stops naming the variable and changes nothing;
   custom values flow through Compose to every mount and variable. Pinned in:
   - Task 1: `test_setup_refuses_invalid_and_overlapping_folders`;
   - Task 1: `test_setup_refuses_old_overrides_naming_the_replacement`;
   - Task 2: `test_compose_mounts_the_data_root_read_only_and_writer_folders_writable`.
4. **Upgrading existing Jira data.** Examples: setup run twice, a `jira` folder that
   already exists, `rag-data/demo` next to `rag-data/jira`, a collection holding
   `rag-data/jira2` or the whole `rag-data` (whose files also start with
   `rag-data/jira/`). Only the `rag-data/jira` folder and its own files move; the
   down migration restores them exactly. Pinned in:
   - Task 1: `test_setup_moves_rag_data_jira_once_and_never_over_an_existing_folder`;
   - Task 3: `test_up_rewrites_only_rag_data_jira_paths_and_down_restores_them`.
5. **Agent read roots after the mount change.** Examples: a collection folder outside
   the old five areas (`exports/tools`), and an active saved agent version whose
   snapshot still holds `/work/code/payments` or `/work/rag-data/jira` (code folders
   that are not mounted make the agent fail). Pinned in:
   - Task 5: `test_agent_config_maps_any_folder_under_the_data_root`;
   - Task 3: `test_saved_agent_versions_follow_the_new_mount`.

## Decisions (where the spec leaves a choice)

- **Compose expresses the mounts with YAML anchors** at the top of
  `deploy/compose.yaml` (`x-data-root`, `x-code-rw`, `x-code-index-rw`,
  `x-generated-docs-rw`, `x-jira-rw`, `x-data-env`). A writable folder is a long-syntax
  bind whose source is `${DATA_ROOT:?…}/${X_FOLDER:-default}` and whose target is
  `/work/data/${X_FOLDER:-default}`, so the default composes with `DATA_ROOT` exactly
  as `.env` does and the nested mount lands on the same relative path inside the
  read-only base. Docker orders mounts by target depth, so the nested bind always
  sits on top of the base. Environment blocks merge `<<: *data-env`. Compose cannot
  check overlap or relative paths; `make setup` does, and `make up` runs setup first.
- **Writable folders per service** keep today's write access exactly, translated to
  the new mounts. The spec's "code processing" row is every service that writes code
  graphs today: `repository-worker` (runs `process_code`), `admin-api` (deletes
  `code-index/<id>` when a project is removed) and `documentation-worker` (keeps its
  writable `code-index` and `generated-documents` mounts, since it writes document
  exports and comparison evidence under `DOCUMENT_OUTPUT_ROOT`). `rag-worker` only
  reads, so only `rag` gets the Jira folder writable.
- **RAG keeps one class, `DataRoot`, and drops the area abstraction.** It holds one
  container root, one host root and the writable folder. Method names and signatures
  used by the worker and poller (`present`, `walk`, `walk_paths`, `read`,
  `container_path`, `host_path`) are kept, so `read(first_segment, rest)` still works;
  the first segment is just a folder now. `ensure_folder` is removed;
  `subfolders(None)` lists the top level. `Workspace.areas` becomes
  `Workspace.data_root`.
- **RAG learns the writable folder from `JIRA_EXPORT_ROOT` relative to
  `DATA_ROOT_DIR`**, the same variables admin-api uses. No `JIRA_FOLDER` variable
  enters a container; the one source of truth is the Compose-computed
  `JIRA_EXPORT_ROOT`. The OS enforces it too: only that folder is writable in `rag`.
- **Writes create subfolders only below the Jira folder.** `DataRoot.write` creates
  missing segments of the file's own path (`jira/2026/DEMO-1.md`), never the Jira
  folder itself or any segment of the collection folder; setup creates the Jira folder.
- **The admin UI learns the Jira folder from `GET /api/jira/tickets`** (and the
  fetch response, which returns the same body) as `export_folder`. The Jira tickets
  page already loads that response, so no new endpoint or request is needed. RAG
  Settings shows it too, from RAG's own `data_root.jira_folder`.
- **Host paths** come from `DATA_ROOT` (host) plus the path relative to
  `DATA_ROOT_DIR`, in RAG (`DataRoot.host_path`) and admin-api
  (`data_root.host_path`). The storage overview's `host_path_relative` flag is
  dropped, since `DATA_ROOT` is always absolute.
- **The setup move removes `rag-data` only when the move left it empty.** Setup made
  it empty, so removing it is part of the move; any remaining content (for example
  `rag-data/demo`) keeps the folder and the warning names `rag-data`.
- **Migration 0067 also rewrites saved agent versions** (`ka.agent_versions.config`):
  `"/work/rag-data/jira` → `"/work/data/jira` and `"/work/(code|code-index|generated-documents|docs)`
  → `"/work/data/…`, followed by `"` or `/`. Without it an active saved version keeps
  container paths that no longer exist and agents with code projects fail
  (`Configured folder is unavailable`). Like the folder rewrite, it assumes the
  default folder names. Test sessions and old runs are history and are left alone.
- **The 0067 down migration restores the old area check `NOT VALID`.** Folders added
  outside the five areas after the upgrade stay; new ones are refused again. This
  keeps the rollback from failing on data it cannot express.
- **Pre-collection agent configs** (folder-era `documents.folders`) keep resolving
  against the fixed historical `/work/rag-data` (`LEGACY_RAG_ROOT` in
  `knowledge_admin/collections.py`). Those configs only name a collection.
- **RAG tests**: the `env` fixture's `roots` maps the five default folder names and
  `"data"` (the base) to paths under `tmp_path/data`, and `DataRoot(data, "/srv/data",
  "jira")`. Existing tests that used `rag-data/<name>` as a writable scratch folder
  now use `jira/<name>` (a mechanical rename); `collection()` creates its folders
  before connecting them, since Add folder no longer does.
- **The `.local` archive move is not code.** It is the last step of Task 10, done by
  hand on this machine only after the user confirms the list.

## Delivery and test boundaries

Tasks land in order on `feature/data-root-folders`, and every suite is green after
each task:
- Task 1 (setup) and Task 2 (Compose) touch only configuration and their tests.
- Task 3's migration must precede Task 4: RAG and admin-api tests run every
  migration, and the old `source_path` check refuses `jira/…` and `exports/…`.
- Tasks 5–7 need Task 3 (folders such as `jira` and `exports/tools` in fixtures).
- Tasks 8–9 need the response shapes from Tasks 4, 6 and 7.

Each task lists the exact tests to run; Task 10 runs every suite.

---

### Task 1: `make setup` — relative folder variables, old overrides refused, the Jira move and the warning

**Files:**
- Modify: `scripts/setup.py` (full new content below)
- Modify: `.env.example` (the data folder block)
- Modify: `tests/test_setup.py`

**Interfaces:**
- Produces, in `scripts/setup.py`: `FOLDERS: dict[str, str]` (variable → default),
  `REMOVED: dict[str, str | None]`, `initialize(root: Path, data_root: str | None = None) -> None`
  (unchanged signature).
- Produces: `.env` gains `CODE_FOLDER`, `CODE_INDEX_FOLDER`, `DOCS_FOLDER`,
  `GENERATED_DOCS_FOLDER`, `JIRA_FOLDER` (defaults from `.env.example`, existing
  values kept). Task 2's Compose file reads them.

- [ ] **Step 1: Write the failing tests**

In `tests/test_setup.py`:

Replace `SUBFOLDERS` and add two imports at the top:

```python
import contextlib
import io
```

```python
SUBFOLDERS = ("code", "code-index", "docs", "generated-documents", "jira")
OLD_OVERRIDES = ("CODE_PATH", "CODE_INDEX_PATH", "RG_DATA", "GENERATED_DOCS_PATH", "DOCS_PATH", "RAG_SOURCE_ROOT")


def run_setup(root: Path, data_root: str | None = None) -> str:
    """initialize() with its printed output captured."""
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        initialize(root, data_root)
    return output.getvalue()
```

Replace `test_setup_creates_data_folders_under_the_root_and_records_it` with:

```python
    def test_setup_creates_data_folders_under_the_root_and_records_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = project(directory)
            data = Path(directory) / "opt" / "knowledge agent"
            output = run_setup(root, str(data))
            values = env_values(root)
            self.assertEqual(values["DATA_ROOT"], f"'{data}'")
            self.assertEqual({key: values[key] for key in ("CODE_FOLDER", "CODE_INDEX_FOLDER", "DOCS_FOLDER",
                                                           "GENERATED_DOCS_FOLDER", "JIRA_FOLDER")},
                             {"CODE_FOLDER": "code", "CODE_INDEX_FOLDER": "code-index", "DOCS_FOLDER": "docs",
                              "GENERATED_DOCS_FOLDER": "generated-documents", "JIRA_FOLDER": "jira"})
            self.assertEqual(sorted(entry.name for entry in data.iterdir()), sorted(SUBFOLDERS))
            self.assertTrue(any((data / "docs").iterdir()))
            self.assertFalse((root / ".local").exists())
            for key in OLD_OVERRIDES:
                self.assertNotIn(key, values)
            self.assertNotIn("Warning", output)
            # Later runs reuse the recorded root without an argument.
            initialize(root)
            self.assertEqual(env_values(root)["DATA_ROOT"], f"'{data}'")
```

Replace `test_setup_rejects_relative_folder_overrides` and
`test_setup_preserves_an_explicit_host_mount` with:

```python
    def test_setup_uses_custom_relative_folders(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = project(directory)
            data = Path(directory) / "data"
            (root / ".env").write_text("CODE_FOLDER=sources/code\nJIRA_FOLDER='exports/jira'\n", encoding="utf-8")
            run_setup(root, str(data))
            self.assertTrue((data / "sources" / "code").is_dir())
            self.assertTrue((data / "exports" / "jira").is_dir())
            self.assertFalse((data / "code").exists() or (data / "jira").exists())
            values = env_values(root)
            self.assertEqual((values["CODE_FOLDER"], values["JIRA_FOLDER"]), ("sources/code", "'exports/jira'"))
            self.assertEqual(values["DOCS_FOLDER"], "docs")

    def test_setup_refuses_invalid_and_overlapping_folders(self) -> None:
        cases = {
            "JIRA_FOLDER=/srv/jira": "JIRA_FOLDER must be a folder inside DATA_ROOT such as jira",
            "CODE_FOLDER=../code": "CODE_FOLDER must be a folder inside DATA_ROOT such as code",
            "CODE_FOLDER=a/../b": "CODE_FOLDER must be a folder",
            "DOCS_FOLDER=.docs": "DOCS_FOLDER must be a folder",
            "DOCS_FOLDER=docs/.git": "DOCS_FOLDER must be a folder",
            "DOCS_FOLDER=a//b": "DOCS_FOLDER must be a folder",
            "DOCS_FOLDER=my docs": "DOCS_FOLDER must be a folder",
            "CODE_INDEX_FOLDER=code/index": "CODE_FOLDER (code) and CODE_INDEX_FOLDER (code/index) overlap",
            "DOCS_FOLDER=jira": "DOCS_FOLDER (jira) and JIRA_FOLDER (jira) overlap",
        }
        for line, message in cases.items():
            with self.subTest(line), tempfile.TemporaryDirectory() as directory:
                root = project(directory)
                (root / ".env").write_text(line + "\n", encoding="utf-8")
                data = Path(directory) / "data"
                with self.assertRaises(SystemExit) as error:
                    initialize(root, str(data))
                self.assertIn(message, str(error.exception))
                self.assertEqual((root / ".env").read_text(encoding="utf-8"), line + "\n")
                self.assertFalse(data.exists())

    def test_setup_refuses_old_overrides_naming_the_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "data"
            cases = {
                f"CODE_PATH='{data}/sources/code'": "CODE_PATH is no longer used. Replace it in .env with CODE_FOLDER=sources/code.",
                "DOCS_PATH=/srv/docs": f"DOCS_PATH is no longer used. Move /srv/docs into {data}, then replace DOCS_PATH in .env with DOCS_FOLDER=<folder inside DATA_ROOT>.",
                "RG_DATA=/srv/rag": f"RG_DATA is no longer used; remove it from .env. Jira exports now go to JIRA_FOLDER inside DATA_ROOT (default jira); make setup moves {data}/rag-data/jira there.",
                "RAG_SOURCE_ROOT=/work/rag-data": "RAG_SOURCE_ROOT is no longer used; remove it from .env.",
            }
            for line, message in cases.items():
                with self.subTest(line):
                    root = Path(tempfile.mkdtemp(dir=directory))
                    shutil.copy(Path(__file__).resolve().parents[1] / ".env.example", root / ".env.example")
                    shutil.copytree(Path(__file__).resolve().parents[1] / "examples", root / "examples")
                    (root / ".env").write_text(line + "\n", encoding="utf-8")
                    with self.assertRaises(SystemExit) as error:
                        initialize(root, str(data))
                    self.assertEqual(str(error.exception), message)
                    self.assertEqual((root / ".env").read_text(encoding="utf-8"), line + "\n")
                    self.assertFalse(data.exists())

    def test_setup_moves_rag_data_jira_once_and_never_over_an_existing_folder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = project(directory)
            data = Path(directory) / "data"
            (data / "rag-data" / "jira").mkdir(parents=True)
            (data / "rag-data" / "jira" / "DEMO-1.md").write_text("# DEMO-1", encoding="utf-8")
            (data / "rag-data" / "demo").mkdir()
            output = run_setup(root, str(data))
            self.assertIn(f"Moved {data / 'rag-data' / 'jira'} to {data / 'jira'}.", output)
            self.assertEqual((data / "jira" / "DEMO-1.md").read_text(encoding="utf-8"), "# DEMO-1")
            self.assertFalse((data / "rag-data" / "jira").exists())
            self.assertTrue((data / "rag-data" / "demo").is_dir())  # other rag-data folders are never touched
            self.assertIn("not configured folders: rag-data.", output)
            again = run_setup(root)
            self.assertNotIn("Moved", again)
            self.assertEqual((data / "jira" / "DEMO-1.md").read_text(encoding="utf-8"), "# DEMO-1")
        with tempfile.TemporaryDirectory() as directory:
            root = project(directory)
            data = Path(directory) / "data"
            (data / "rag-data" / "jira").mkdir(parents=True)
            (data / "rag-data" / "jira" / "OLD-1.md").write_text("old", encoding="utf-8")
            (data / "jira").mkdir()
            (data / "jira" / "NEW-1.md").write_text("new", encoding="utf-8")
            output = run_setup(root, str(data))
            self.assertNotIn("Moved", output)
            self.assertEqual((data / "rag-data" / "jira" / "OLD-1.md").read_text(encoding="utf-8"), "old")
            self.assertEqual(sorted(p.name for p in (data / "jira").iterdir()), ["NEW-1.md"])
        with tempfile.TemporaryDirectory() as directory:
            root = project(directory)
            data = Path(directory) / "data"
            (data / "rag-data" / "jira").mkdir(parents=True)
            output = run_setup(root, str(data))
            self.assertIn(f"Removed the empty folder {data / 'rag-data'}.", output)
            self.assertFalse((data / "rag-data").exists())
            self.assertNotIn("Warning", output)

    def test_setup_warns_about_other_top_level_entries_and_moves_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = project(directory)
            data = Path(directory) / "data"
            (data / "backups").mkdir(parents=True)
            (data / "users").mkdir()
            (data / "startup.log").write_text("log", encoding="utf-8")
            output = run_setup(root, str(data))
            self.assertIn(f"Warning: {data} holds entries that are not configured folders: backups, startup.log, users. "
                          "RAG can see every folder in DATA_ROOT; move them out, for example to a sibling DATA_ARCHIVE "
                          "folder. Nothing was moved.", output)
            self.assertTrue((data / "backups").is_dir() and (data / "users").is_dir())
            self.assertEqual((data / "startup.log").read_text(encoding="utf-8"), "log")
```

In `test_setup_preserves_credentials_and_existing_docs`, keep everything; it already
checks that a second run leaves `.env` byte-identical (idempotency).

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `python3 -m unittest discover -s tests -v`
Expected: FAIL. `.env` has no `CODE_FOLDER`, `rag-data` is created instead of `jira`,
no old override is refused, and nothing is moved or warned about.
(`test_compose_mount_and_display_use_the_same_literal_host_path` still passes; Task 2
replaces it.)

- [ ] **Step 3: Replace `scripts/setup.py`**

```python
"""Prepare independent local configuration without importing v2 secrets or data."""

import base64
import os
import re
import secrets
import shutil
from pathlib import Path


# Folders inside DATA_ROOT, by .env variable, with their defaults. Compose mounts DATA_ROOT once at
# /work/data and computes each service's root from these (CODE_ROOT=/work/data/${CODE_FOLDER}, ...).
FOLDERS = {
    "CODE_FOLDER": "code",
    "CODE_INDEX_FOLDER": "code-index",
    "DOCS_FOLDER": "docs",
    "GENERATED_DOCS_FOLDER": "generated-documents",
    "JIRA_FOLDER": "jira",
}
# Absolute overrides from before the one data folder, and the variable that replaces each.
REMOVED = {
    "CODE_PATH": "CODE_FOLDER",
    "CODE_INDEX_PATH": "CODE_INDEX_FOLDER",
    "RG_DATA": "JIRA_FOLDER",
    "GENERATED_DOCS_PATH": "GENERATED_DOCS_FOLDER",
    "DOCS_PATH": "DOCS_FOLDER",
    "RAG_SOURCE_ROOT": None,
}
SEGMENT = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]*")  # the same rule as a RAG source_path segment
OLD_JIRA = Path("rag-data") / "jira"
USAGE = "make setup DATA_ROOT=/opt/knowledge-agent"


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1].replace("\\'", "'")
    return value


def _quote(path: Path) -> str:
    # Single quotes keep spaces, dollar signs, and # literal in Compose's .env.
    return "'" + str(path).replace("'", "\\'") + "'"


def _absolute(name: str, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise SystemExit(f"{name} must be an absolute host path, got {value!r}. Example: {USAGE}")
    return path


def _refuse_removed(data: Path, current: dict[str, str]) -> None:
    """Stop when an absolute override from before the one data folder is still set."""
    for key, replacement in REMOVED.items():
        value = _unquote(current.get(key, ""))
        if not value:
            continue
        if replacement is None:
            raise SystemExit(f"{key} is no longer used; remove it from .env.")
        if key == "RG_DATA":
            raise SystemExit("RG_DATA is no longer used; remove it from .env. Jira exports now go to JIRA_FOLDER inside "
                             f"DATA_ROOT (default jira); make setup moves {data / OLD_JIRA} there.")
        path = Path(value).expanduser()
        if path.is_absolute() and path != data and path.is_relative_to(data):
            raise SystemExit(f"{key} is no longer used. Replace it in .env with {replacement}={path.relative_to(data).as_posix()}.")
        raise SystemExit(f"{key} is no longer used. Move {value} into {data}, then replace {key} in .env with "
                         f"{replacement}=<folder inside DATA_ROOT>.")


def _folders(current: dict[str, str], defaults: dict[str, str]) -> dict[str, str]:
    """The five folder variables as validated relative paths; overlap is refused."""
    folders = {}
    for key, default in FOLDERS.items():
        value = _unquote(current.get(key, "")) or _unquote(defaults.get(key, "")) or default
        if not all(SEGMENT.fullmatch(part) for part in value.split("/")):
            raise SystemExit(f"{key} must be a folder inside DATA_ROOT such as {default}: a relative path without a "
                             f"leading /, .., hidden or empty segments; got {value!r}.")
        folders[key] = value
    keys = list(folders)
    for index, first in enumerate(keys):
        for second in keys[index + 1:]:
            a, b = folders[first], folders[second]
            if a == b or a.startswith(b + "/") or b.startswith(a + "/"):
                raise SystemExit(f"{first} ({a}) and {second} ({b}) overlap; choose separate folders inside DATA_ROOT.")
    return folders


def _create(data: Path, folders: list[Path]) -> None:
    for folder in folders:
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            raise SystemExit(
                f"Cannot create {folder}. Create the data folder once with:\n"
                f"  sudo mkdir -p {data} && sudo chown $(id -u):$(id -g) {data}"
            ) from None


def _move_jira(data: Path, jira: str) -> None:
    """Move DATA_ROOT/rag-data/jira to the Jira folder once; never over an existing folder."""
    old, new = data / OLD_JIRA, data / jira
    if not old.is_dir() or old.is_symlink() or new.exists() or new.is_symlink():
        return
    new.parent.mkdir(parents=True, exist_ok=True)
    old.rename(new)
    print(f"Moved {old} to {new}.")
    try:
        old.parent.rmdir()  # only succeeds when the move left rag-data empty
    except OSError:
        return
    print(f"Removed the empty folder {old.parent}.")


def _warn_other_entries(data: Path, folders: dict[str, str]) -> None:
    """Name every top-level entry that is not a configured folder; nothing is moved."""
    configured = {folder.split("/")[0] for folder in folders.values()}
    others = sorted(entry.name for entry in data.iterdir() if entry.name not in configured)
    if others:
        print(f"Warning: {data} holds entries that are not configured folders: {', '.join(others)}. "
              "RAG can see every folder in DATA_ROOT; move them out, for example to a sibling DATA_ARCHIVE folder. "
              "Nothing was moved.")


def initialize(root: Path, data_root: str | None = None) -> None:
    env_path = root / ".env"
    template = (root / ".env.example").read_text(encoding="utf-8")
    text = env_path.read_text(encoding="utf-8") if env_path.exists() else template
    defaults = dict(re.findall(r"^([A-Z][A-Z0-9_]*)=(.*)$", template, re.MULTILINE))
    current = dict(re.findall(r"^([A-Z][A-Z0-9_]*)=(.*)$", text, re.MULTILINE))
    data_root = data_root or _unquote(current.get("DATA_ROOT", ""))
    if not data_root:
        raise SystemExit(f"Choose where the application keeps its files: {USAGE}")
    data = _absolute("DATA_ROOT", data_root)
    _refuse_removed(data, current)
    folders = _folders(current, defaults)
    _create(data, [data])
    _move_jira(data, folders["JIRA_FOLDER"])
    _create(data, [data / folder for folder in folders.values()])
    defaults["DATA_ROOT"] = _quote(data)
    secret_keys = {
        "POSTGRES_PASSWORD", "KEYCLOAK_ADMIN_PASSWORD", "APP_ADMIN_PASSWORD",
        "OAUTH2_PROXY_CLIENT_SECRET", "OAUTH2_PROXY_COOKIE_SECRET", "PHOENIX_SECRET",
        "ADMIN_OAUTH2_PROXY_CLIENT_SECRET", "ADMIN_OAUTH2_PROXY_COOKIE_SECRET",
        "PHOENIX_ADMIN_PASSWORD", "PHOENIX_ADMIN_SECRET", "LITELLM_MASTER_KEY",
        "CREDENTIAL_ENCRYPTION_KEY", "RAG_BACKOFFICE_KEY", "READER_AGENT_KEY",
    }
    for key, default in defaults.items():
        existing = current.get(key, "").strip().strip("\"'")
        if key == "DATA_ROOT":
            if current.get(key) == default:
                continue
        elif key in current and (existing or key not in secret_keys):
            continue
        value = default
        if key in secret_keys:
            value = secrets.token_urlsafe(32)
            if key.endswith("OAUTH2_PROXY_COOKIE_SECRET") or key == "CREDENTIAL_ENCRYPTION_KEY":
                value = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
            elif key == "LITELLM_MASTER_KEY":
                value = "sk-" + value
        if key in current:
            text = re.sub(rf"^{key}=.*$", lambda _: f"{key}={value}", text, flags=re.MULTILINE)
        else:
            text = text.rstrip() + f"\n{key}={value}\n"

    # The temporary file is private from creation; atomic replacement never
    # leaves a partially written secrets file if setup is interrupted.
    temporary = root / f".env.setup-{secrets.token_hex(8)}"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(text)
    temporary.replace(env_path)

    docs_path = data / folders["DOCS_FOLDER"]
    if not any(docs_path.iterdir()):
        shutil.copytree(root / "examples" / "docs", docs_path, dirs_exist_ok=True)
    _warn_other_entries(data, folders)
    print(f"Local configuration ready. Credentials are in .env (mode 0600). Files are kept in {data}.")
    print("Run make up, then make smoke to verify login and services. Load and process content through RAG → Collections.")


if __name__ == "__main__":
    initialize(Path(__file__).resolve().parents[1], os.environ.get("DATA_ROOT") or None)
```

- [ ] **Step 4: Update `.env.example`**

Replace the block from `# Absolute host folder for the application's files; set it with`
through `# DOCS_PATH=/srv/docs` with:

```
# Absolute host folder for the application's files; set it with
#   make setup DATA_ROOT=/opt/knowledge-agent
# The database, Keycloak and Phoenix keep their data in Docker volumes.
DATA_ROOT=
# Folders inside DATA_ROOT, as relative paths: no leading /, no .., no hidden
# segments, and none inside another. RAG reads all of DATA_ROOT; each service
# writes only its own folder. Keep anything else (backups, logs) outside DATA_ROOT.
CODE_FOLDER=code
CODE_INDEX_FOLDER=code-index
DOCS_FOLDER=docs
GENERATED_DOCS_FOLDER=generated-documents
JIRA_FOLDER=jira
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `python3 -m unittest discover -s tests -v`
Expected: PASS, including the unchanged
`test_compose_mount_and_display_use_the_same_literal_host_path`.

- [ ] **Step 6: Commit**

```bash
git add scripts/setup.py .env.example tests/test_setup.py
git commit -m "feat(setup): relative data folders inside DATA_ROOT, old overrides refused, Jira folder move

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Compose — one read-only `DATA_ROOT` mount, writable folders only for writers

**Files:**
- Modify: `deploy/compose.yaml`
- Modify: `services/docs-wiki/tests/test_deploy.py`
- Modify: `tests/test_setup.py` (replace the rendered Compose test)

**Interfaces:**
- Consumes: the `.env` folder variables from Task 1.
- Produces, in every data-using container: `/work/data` (read-only) and the
  in-container variables listed in the Global Constraints. Tasks 4–7 read
  `DATA_ROOT_DIR`, `DATA_ROOT`, `JIRA_EXPORT_ROOT` and the existing `*_ROOT` variables.

- [ ] **Step 1: Write the failing tests**

In `services/docs-wiki/tests/test_deploy.py`, replace `host()` with:

```python
DATA = {"type": "bind", "source": ROOT, "target": "/work/data", "read_only": True}
FOLDERS = {"CODE_FOLDER": "code", "CODE_INDEX_FOLDER": "code-index", "DOCS_FOLDER": "docs",
           "GENERATED_DOCS_FOLDER": "generated-documents", "JIRA_FOLDER": "jira"}
WRITERS = {"rag": ["JIRA_FOLDER"], "rag-worker": [], "docs-wiki": [], "shell-worker": [],
           "admin-api": ["CODE_FOLDER", "CODE_INDEX_FOLDER", "GENERATED_DOCS_FOLDER"],
           "repository-worker": ["CODE_FOLDER", "CODE_INDEX_FOLDER"],
           "documentation-worker": ["CODE_INDEX_FOLDER", "GENERATED_DOCS_FOLDER"]}


def folder(variable: str) -> str:
    return "${" + variable + ":-" + FOLDERS[variable] + "}"


def writable(variable: str) -> dict:
    """A writable folder of DATA_ROOT, bound over the read-only base at the same relative path."""
    return {"type": "bind", "source": ROOT + "/" + folder(variable), "target": "/work/data/" + folder(variable)}


def data_mounts(service: dict) -> list:
    return [v for v in service.get("volumes", []) if isinstance(v, dict) and v["target"].startswith("/work/data")]
```

Replace `test_repository_worker_has_exclusive_write_mount_and_credentials` with:

```python
def test_repository_worker_has_its_write_mounts_and_credentials():
    services = yaml.safe_load((DEPLOY / "compose.yaml").read_text())["services"]
    worker = services["repository-worker"]
    assert worker["volumes"] == [DATA, writable("CODE_FOLDER"), writable("CODE_INDEX_FOLDER")]
    assert worker["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    for name in ("admin-api", "repository-worker"):
        assert "CREDENTIAL_ENCRYPTION_KEY" in services[name]["environment"]
    for name in ("admin-web", "web"):
        assert "CREDENTIAL_ENCRYPTION_KEY" not in services[name].get("environment", {})
```

Replace `test_agent_config_paths_match_code_and_document_mounts`,
`test_rag_reads_every_data_area_and_writes_only_rag_data` and
`test_shell_worker_reads_every_collection_area_read_only` with:

```python
def test_data_root_is_mounted_once_read_only_with_writable_folders_only_for_writers():
    services = yaml.safe_load((DEPLOY / "compose.yaml").read_text())["services"]
    for name, variables in WRITERS.items():
        assert data_mounts(services[name]) == [DATA, *(writable(v) for v in variables)], name
    for name, service in services.items():
        if name not in WRITERS:
            assert not data_mounts(service), name
        for volume in service.get("volumes", []):
            target = volume["target"] if isinstance(volume, dict) else volume.split(":")[1]
            assert (not target.startswith("/work/") or target.startswith("/work/data")
                    or target == "/work/agent-workspaces"), (name, target)


def test_services_compute_their_roots_from_the_folder_variables():
    services = yaml.safe_load((DEPLOY / "compose.yaml").read_text())["services"]
    expected = {"DATA_ROOT_DIR": "/work/data", "DATA_ROOT": ROOT,
                "CODE_ROOT": "/work/data/" + folder("CODE_FOLDER"),
                "CODE_INDEX_ROOT": "/work/data/" + folder("CODE_INDEX_FOLDER"),
                "BOOTSTRAP_DOCS_ROOT": "/work/data/" + folder("DOCS_FOLDER"),
                "DOCUMENT_OUTPUT_ROOT": "/work/data/" + folder("GENERATED_DOCS_FOLDER"),
                "JIRA_EXPORT_ROOT": "/work/data/" + folder("JIRA_FOLDER")}
    removed = {"RAG_SOURCE_ROOT", "RG_DATA", "CODE_PATH", "CODE_INDEX_PATH", "GENERATED_DOCS_PATH", "DOCS_PATH"}
    for name in ("rag", "rag-worker", "admin-api", "repository-worker", "documentation-worker"):
        env = services[name]["environment"]
        assert {key: env[key] for key in expected} == expected, name
        assert not removed & set(env), name
    assert "environment" not in services["shell-worker"]  # it reads DATA_ROOT_DIR's default, /work/data
```

Keep `test_host_folders_come_from_data_root_not_the_checkout` unchanged.

In `tests/test_setup.py`, replace `test_compose_mount_and_display_use_the_same_literal_host_path`
with:

```python
    def test_compose_mounts_the_data_root_read_only_and_writer_folders_writable(self) -> None:
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project with $dollars #and 'quotes'"
            root.mkdir()
            shutil.copy(source / ".env.example", root / ".env.example")
            shutil.copytree(source / "examples", root / "examples")
            (root / ".env").write_text("CODE_FOLDER=src/code\nJIRA_FOLDER=exports/jira\n", encoding="utf-8")
            run_setup(root, str(root / "data"))
            result = subprocess.run(
                ["docker", "compose", "--env-file", str(root / ".env"), "-f", str(source / "deploy/compose.yaml"), "config", "--format", "json"],
                capture_output=True, text=True, check=True,
            )
            services = json.loads(result.stdout)["services"]
            # Compose escapes dollars when serializing config for safe reloading.
            data = str(root / "data").replace("$", "$$")
            folders = {"code": "src/code", "index": "code-index", "documents": "generated-documents", "jira": "exports/jira"}
            writers = {"rag": ["jira"], "rag-worker": [], "docs-wiki": [], "shell-worker": [],
                       "admin-api": ["code", "index", "documents"], "repository-worker": ["code", "index"],
                       "documentation-worker": ["index", "documents"]}
            for name, writable in writers.items():
                mounts = sorted((v["source"], v["target"], bool(v.get("read_only"))) for v in services[name]["volumes"]
                                if v["target"].startswith("/work/data"))
                expected = sorted([(data, "/work/data", True)] + [
                    (f"{data}/{folders[key]}", f"/work/data/{folders[key]}", False) for key in writable])
                self.assertEqual(mounts, expected, name)
            for name in ("rag", "admin-api"):
                env = services[name]["environment"]
                self.assertEqual(env["DATA_ROOT_DIR"], "/work/data")
                self.assertEqual(env["DATA_ROOT"], data)
                self.assertEqual(env["CODE_ROOT"], "/work/data/src/code")
                self.assertEqual(env["JIRA_EXPORT_ROOT"], "/work/data/exports/jira")
                self.assertEqual(env["DOCUMENT_OUTPUT_ROOT"], "/work/data/generated-documents")
            for service in services.values():
                for volume in service.get("volumes", []):
                    self.assertNotIn(".local", volume.get("source", ""))
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run:
```bash
cd services/docs-wiki && uv run --frozen pytest tests/test_deploy.py -q
cd ../.. && python3 -m unittest tests.test_setup -v
```
Expected: FAIL. The services still mount `/work/code`, `/work/rag-data`, … and have
no `DATA_ROOT_DIR`.

- [ ] **Step 3: Rewrite the data mounts in `deploy/compose.yaml`**

Insert after `name: knowledge-agent-v3` (before `services:`):

```yaml
# DATA_ROOT is mounted once, read-only, at /work/data. A service that writes gets that
# folder as a writable bind on top, at the same relative path. make setup validates the
# folder variables (relative, no overlap); Compose cannot.
x-data-root: &data-root
  type: bind
  source: ${DATA_ROOT:?Run make setup DATA_ROOT=/path}
  target: /work/data
  read_only: true
x-code-rw: &code-rw
  type: bind
  source: ${DATA_ROOT:?Run make setup DATA_ROOT=/path}/${CODE_FOLDER:-code}
  target: /work/data/${CODE_FOLDER:-code}
x-code-index-rw: &code-index-rw
  type: bind
  source: ${DATA_ROOT:?Run make setup DATA_ROOT=/path}/${CODE_INDEX_FOLDER:-code-index}
  target: /work/data/${CODE_INDEX_FOLDER:-code-index}
x-generated-docs-rw: &generated-docs-rw
  type: bind
  source: ${DATA_ROOT:?Run make setup DATA_ROOT=/path}/${GENERATED_DOCS_FOLDER:-generated-documents}
  target: /work/data/${GENERATED_DOCS_FOLDER:-generated-documents}
x-jira-rw: &jira-rw
  type: bind
  source: ${DATA_ROOT:?Run make setup DATA_ROOT=/path}/${JIRA_FOLDER:-jira}
  target: /work/data/${JIRA_FOLDER:-jira}
x-data-env: &data-env
  DATA_ROOT_DIR: /work/data
  DATA_ROOT: ${DATA_ROOT:?Run make setup DATA_ROOT=/path}
  CODE_ROOT: /work/data/${CODE_FOLDER:-code}
  CODE_INDEX_ROOT: /work/data/${CODE_INDEX_FOLDER:-code-index}
  BOOTSTRAP_DOCS_ROOT: /work/data/${DOCS_FOLDER:-docs}
  DOCUMENT_OUTPUT_ROOT: /work/data/${GENERATED_DOCS_FOLDER:-generated-documents}
  JIRA_EXPORT_ROOT: /work/data/${JIRA_FOLDER:-jira}
```

Then, per service (keep every other key exactly as it is):

- `rag`: `volumes:` becomes `[*data-root, *jira-rw]` (block style:
  `- *data-root` / `- *jira-rw`). In `environment:` delete `RAG_SOURCE_ROOT`,
  `CODE_ROOT`, `CODE_INDEX_ROOT`, `DOCUMENT_OUTPUT_ROOT`, `BOOTSTRAP_DOCS_ROOT`,
  `CODE_PATH`, `CODE_INDEX_PATH`, `GENERATED_DOCS_PATH`, `DOCS_PATH`, `RG_DATA`, and
  add `<<: *data-env` as the first entry.
- `rag-worker`: `volumes:` becomes `- *data-root`. The same `environment:` deletions
  and `<<: *data-env`.
- `docs-wiki`: `volumes:` becomes `- *data-root` and `- wiki-data:/data`.
- `admin-api`: `volumes:` becomes
  ```yaml
      volumes:
        - *data-root
        - *code-rw
        - *code-index-rw
        - *generated-docs-rw
        - wiki-data:/data
        - agent-workspaces:/work/agent-workspaces
  ```
  In `environment:` delete `CODE_INDEX_ROOT`, `CODE_INDEX_PATH`,
  `DOCUMENT_OUTPUT_ROOT`, `GENERATED_DOCS_PATH`, `BOOTSTRAP_DOCS_ROOT`, `DOCS_PATH`,
  `RG_DATA`, `CODE_ROOT`, `RAG_SOURCE_ROOT`, `CODE_PATH`, and add `<<: *data-env`
  as the first entry.
- `repository-worker`: `volumes:` becomes `- *data-root`, `- *code-rw`,
  `- *code-index-rw`. In `environment:` delete `CODE_INDEX_ROOT` and `CODE_ROOT` and
  add `<<: *data-env`.
- `documentation-worker`: `volumes:` becomes `- *data-root`, `- *code-index-rw`,
  `- *generated-docs-rw`, `- agent-workspaces:/work/agent-workspaces`. In
  `environment:` delete `CODE_INDEX_ROOT`, `DOCUMENT_OUTPUT_ROOT`, `CODE_ROOT`,
  `RAG_SOURCE_ROOT` and add `<<: *data-env`.
- `shell-worker`: `volumes:` becomes `- *data-root` and
  `- agent-workspaces:/work/agent-workspaces`. It keeps no `environment:` block.

- [ ] **Step 4: Run the tests and confirm they pass**

Run:
```bash
cd services/docs-wiki && uv run --frozen pytest tests/test_deploy.py -q
cd ../.. && python3 -m unittest tests.test_setup -v && make compose-validate
```
Expected: PASS; `make compose-validate` prints nothing and exits 0.

- [ ] **Step 5: Commit**

```bash
git add deploy/compose.yaml services/docs-wiki/tests/test_deploy.py tests/test_setup.py
git commit -m "feat(deploy): mount DATA_ROOT once read-only with writable folders only for writers

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Migration 0067 — any visible collection folder, Jira paths move to `jira`

**Files:**
- Create: `db/migrations/0067_data_root_folders.up.sql`
- Create: `db/migrations/0067_data_root_folders.down.sql`
- Create: `services/admin-api/tests/test_data_root_folders_migration.py`
- Modify: `db/tests/run.sh` (rollback pin `65` → `66`)

**Interfaces:**
- Consumes: the 0066 schema (`ka.rag_collection_paths`, `ka.rag_files`,
  `ka.rag_file_log`, `ka.agent_versions`).
- Produces: `rag_collection_paths_source_path_check` with the generic segment regex;
  rewritten Jira paths and saved agent version paths.

- [ ] **Step 1: Write the failing test**

Create `services/admin-api/tests/test_data_root_folders_migration.py`:

```python
"""0067 opens collection folders to any visible path under DATA_ROOT, moves Jira paths from rag-data/jira to
jira, and follows saved agent versions to the single /work/data mount."""
from pathlib import Path

import pytest
from psycopg.types.json import Jsonb

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0067_data_root_folders.up.sql', '0067_data_root_folders.down.sql'
SAVED = {'code': {'projects': [{'folder_paths': ['/work/code/payments']}]},
         'documents': {'collections': [{'name': 'jira', 'paths': [
             '/work/rag-data/jira', '/work/rag-data/jira2', '/work/rag-data/demo', '/work/code-index/x',
             '/work/generated-documents', '/work/docs/runbooks']}]}}
MOVED = {'code': {'projects': [{'folder_paths': ['/work/data/code/payments']}]},
         'documents': {'collections': [{'name': 'jira', 'paths': [
             '/work/data/jira', '/work/rag-data/jira2', '/work/rag-data/demo', '/work/data/code-index/x',
             '/work/data/generated-documents', '/work/data/docs/runbooks']}]}}
FILES_BEFORE = {'jira': 'rag-data/jira/DEMO-1.md', 'lookalike': 'rag-data/jira2/x.md', 'whole': 'rag-data/jira/DEMO-1.md'}
BEFORE = {'folders': {'jira': 'rag-data/jira', 'lookalike': 'rag-data/jira2', 'whole': 'rag-data'},
          'files': FILES_BEFORE, 'log': FILES_BEFORE}
FILES_AFTER = {**FILES_BEFORE, 'jira': 'jira/DEMO-1.md'}
AFTER = {'folders': {**BEFORE['folders'], 'jira': 'jira'}, 'files': FILES_AFTER, 'log': FILES_AFTER}


def run(conn, name: str):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def state(conn) -> dict:
    def by_name(sql: str) -> dict:
        return {r['name']: r['value'] for r in conn.execute(sql).fetchall()}
    return {
        'folders': by_name("""SELECT c.name, p.source_path AS value FROM ka.rag_collection_paths p
            JOIN ka.rag_collections c ON c.id=p.collection_id WHERE c.name IN ('jira','lookalike','whole')"""),
        'files': by_name("""SELECT c.name, f.path AS value FROM ka.rag_files f
            JOIN ka.rag_collections c ON c.id=f.collection_id"""),
        'log': by_name("""SELECT c.name, l.path AS value FROM ka.rag_file_log l
            JOIN ka.rag_collections c ON c.id=l.collection_id"""),
    }


@pytest.fixture
def at_0066(store):
    """Rolled back to 0066 with the jira folder, a look-alike, a whole rag-data folder that shares the Jira file,
    their file log rows and a saved agent version."""
    with store.connect() as conn:
        run(conn, DOWN)
        project = store.project(conn, 'shop')
        conn.execute("""INSERT INTO ka.rag_workspaces(project_id,settings) VALUES (%s,'{"max_file_bytes":2000000}')
            ON CONFLICT (project_id) DO NOTHING""", (project,))
        ids = {name: conn.execute("INSERT INTO ka.rag_collections(project_id,name) VALUES (%s,%s) RETURNING id",
                                  (project, name)).fetchone()['id'] for name in ('jira', 'lookalike', 'whole')}
        job = conn.execute("""INSERT INTO ka.jobs(project_id,kind,rag_collection_id,input,status,started_at,finished_at)
            VALUES (%s,'rag_collection_sync',%s,'{}','succeeded',now(),now()) RETURNING id""",
            (project, ids['jira'])).fetchone()['id']
        for name, source in BEFORE['folders'].items():
            path_id = conn.execute("""INSERT INTO ka.rag_collection_paths(collection_id,source_path)
                VALUES (%s,%s) RETURNING id""", (ids[name], source)).fetchone()['id']
            conn.execute("""INSERT INTO ka.rag_files(project_id,collection_id,path_id,path,content_hash,processing_hash,
                embedding_profile,job_id,indexed_at) VALUES (%s,%s,%s,%s,repeat('a',64),repeat('b',64),'',%s,now())""",
                (project, ids[name], path_id, FILES_BEFORE[name], job))
            conn.execute("INSERT INTO ka.rag_file_log(collection_id,path_id,path,status) VALUES (%s,%s,%s,'indexed')",
                         (ids[name], path_id, FILES_BEFORE[name]))
        actor = store.actor(conn, 'migration-admin', 'migration@test')
        version = conn.execute("""INSERT INTO ka.agent_versions(agent,version,label,config,created_by)
            VALUES ('search',1000,'Saved before 0067',%s,%s) RETURNING id""", (Jsonb(SAVED), actor)).fetchone()['id']
        yield conn, ids, version


def test_up_rewrites_only_rag_data_jira_paths_and_down_restores_them(at_0066):
    conn, _, _ = at_0066
    assert state(conn) == BEFORE
    run(conn, UP)
    assert state(conn) == AFTER
    run(conn, DOWN)
    assert state(conn) == BEFORE


def test_collection_folders_may_be_any_visible_path(at_0066):
    conn, ids, _ = at_0066
    insert = "INSERT INTO ka.rag_collection_paths(collection_id,source_path) VALUES (%s,%s)"
    with pytest.raises(Exception, match='rag_collection_paths_source_path_check'):
        with conn.transaction():
            conn.execute(insert, (ids['lookalike'], 'exports/tools'))
    run(conn, UP)
    conn.execute(insert, (ids['lookalike'], 'exports/tools'))
    for bad in ('.hidden', 'a/../b', '/abs', 'a//b', 'a/', 'a b', 'x' * 501):
        with pytest.raises(Exception, match='rag_collection_paths_source_path_check'):
            with conn.transaction():
                conn.execute(insert, (ids['lookalike'], bad))
    run(conn, DOWN)  # a folder added after the upgrade is kept; new ones outside the five areas are refused again
    assert conn.execute("SELECT count(*) AS n FROM ka.rag_collection_paths WHERE source_path='exports/tools'").fetchone()['n'] == 1
    with pytest.raises(Exception, match='rag_collection_paths_source_path_check'):
        with conn.transaction():
            conn.execute(insert, (ids['whole'], 'other/x'))


def test_saved_agent_versions_follow_the_new_mount(at_0066):
    conn, _, version = at_0066
    def config():
        return conn.execute("SELECT config FROM ka.agent_versions WHERE id=%s", (version,)).fetchone()['config']
    run(conn, UP)
    assert config() == MOVED
    run(conn, DOWN)
    assert config() == SAVED
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_data_root_folders_migration.py -q`
Expected: FAIL with `FileNotFoundError` for `0067_data_root_folders.down.sql`.

- [ ] **Step 3: Write the migration**

`db/migrations/0067_data_root_folders.up.sql`:

```sql
SET search_path = ka, public;

-- A collection folder is any visible path under DATA_ROOT; the first segment is no longer one of five areas.
ALTER TABLE rag_collection_paths DROP CONSTRAINT rag_collection_paths_source_path_check;
ALTER TABLE rag_collection_paths ADD CONSTRAINT rag_collection_paths_source_path_check CHECK (
    length(source_path) <= 500 AND source_path ~ '^[A-Za-z0-9_-][A-Za-z0-9._-]*(/[A-Za-z0-9_-][A-Za-z0-9._-]*)*$');

-- Jira exports move from rag-data/jira to the default JIRA_FOLDER, jira (make setup moves the files).
-- Only that folder and its own files change: rag-data/jira2 or a whole rag-data folder stay as they are.
UPDATE rag_collection_paths SET source_path = substr(source_path, length('rag-data/') + 1)
    WHERE source_path = 'rag-data/jira' OR source_path LIKE 'rag-data/jira/%';
UPDATE rag_files f SET path = substr(f.path, length('rag-data/') + 1) FROM rag_collection_paths p
    WHERE p.id = f.path_id AND (p.source_path = 'jira' OR p.source_path LIKE 'jira/%') AND f.path LIKE 'rag-data/jira/%';
UPDATE rag_file_log l SET path = substr(l.path, length('rag-data/') + 1) FROM rag_collection_paths p
    WHERE p.id = l.path_id AND (p.source_path = 'jira' OR p.source_path LIKE 'jira/%') AND l.path LIKE 'rag-data/jira/%';

-- Saved agent versions hold container paths; DATA_ROOT is now mounted once at /work/data.
UPDATE agent_versions SET config = regexp_replace(regexp_replace(config::text,
        '"/work/rag-data/jira(["/])', '"/work/data/jira\1', 'g'),
        '"/work/(code|code-index|generated-documents|docs)(["/])', '"/work/data/\1\2', 'g')::jsonb
    WHERE config::text ~ '"/work/(rag-data/jira|code|code-index|generated-documents|docs)["/]';
```

`db/migrations/0067_data_root_folders.down.sql`:

```sql
SET search_path = ka, public;

UPDATE agent_versions SET config = regexp_replace(regexp_replace(config::text,
        '"/work/data/jira(["/])', '"/work/rag-data/jira\1', 'g'),
        '"/work/data/(code|code-index|generated-documents|docs)(["/])', '"/work/\1\2', 'g')::jsonb
    WHERE config::text ~ '"/work/data/(jira|code|code-index|generated-documents|docs)["/]';

UPDATE rag_files f SET path = 'rag-data/' || f.path FROM rag_collection_paths p
    WHERE p.id = f.path_id AND (p.source_path = 'jira' OR p.source_path LIKE 'jira/%') AND f.path LIKE 'jira/%';
UPDATE rag_file_log l SET path = 'rag-data/' || l.path FROM rag_collection_paths p
    WHERE p.id = l.path_id AND (p.source_path = 'jira' OR p.source_path LIKE 'jira/%') AND l.path LIKE 'jira/%';
UPDATE rag_collection_paths SET source_path = 'rag-data/' || source_path
    WHERE source_path = 'jira' OR source_path LIKE 'jira/%';

-- Folders added outside the five areas since the upgrade are kept; only new ones are refused again.
ALTER TABLE rag_collection_paths DROP CONSTRAINT rag_collection_paths_source_path_check;
ALTER TABLE rag_collection_paths ADD CONSTRAINT rag_collection_paths_source_path_check CHECK (
    length(source_path) <= 500 AND source_path ~
    '^(code|code-index|rag-data|generated-documents|docs)(/[A-Za-z0-9_-][A-Za-z0-9._-]*)*$') NOT VALID;
```

In `db/tests/run.sh`, change `[ "$rolled_back_version" = 65 ]` to
`[ "$rolled_back_version" = 66 ]`.

- [ ] **Step 4: Run the tests and confirm they pass**

Run:
```bash
cd services/admin-api && uv run --frozen pytest tests/test_data_root_folders_migration.py \
  tests/test_rag_managed_collections_migration.py tests/test_rag_collection_paths_migration.py -q
cd ../.. && bash db/tests/run.sh
```
Expected: PASS; `run.sh` prints its `PASS:` lines.

- [ ] **Step 5: Commit**

```bash
git add db/migrations/0067_data_root_folders.up.sql db/migrations/0067_data_root_folders.down.sql \
  db/tests/run.sh services/admin-api/tests/test_data_root_folders_migration.py
git commit -m "feat(db): 0067 any visible collection folder, Jira paths move from rag-data/jira to jira

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: RAG — one data root, any top-level folder, writes only in the Jira folder

**Files:**
- Modify: `services/rag/src/rag/collections.py` (`Areas` → `DataRoot`, code below)
- Modify: `services/rag/src/rag/workspace.py` (`Workspace.data_root`)
- Modify: `services/rag/src/rag/workspace_api.py`
- Modify: `services/rag/src/rag/workspace_worker.py`, `services/rag/src/rag/workspace_poll.py` (rename only)
- Modify: `services/rag/tests/conftest.py`, `test_workspace.py`, `test_collections.py`,
  `test_paths_api.py`, `test_collections_api.py`, `test_cli.py`,
  `test_embedding_migration.py`, and (rename only) `test_sync.py`, `test_file_log.py`,
  `test_polling.py`, `test_collection_search.py`

**Interfaces:**
- Consumes: `DATA_ROOT_DIR`, `DATA_ROOT`, `JIRA_EXPORT_ROOT` (Task 2); the 0067 check
  (Task 3).
- Produces, in `rag.collections`:
  - `source_path(value: str) -> str` (generic segment rule);
  - `DataRoot(root: Path | None, host_root: str | None = None, jira_folder: str | None = None)`
    with `.root`, `.host_root`, `.jira_folder`, `DataRoot.from_env()`,
    `container_path(source) -> Path`, `host_path(source) -> str | None`,
    `writable(source) -> bool`, `present(source) -> bool`,
    `subfolders(source: str | None, limit=MAX_FOLDERS) -> tuple[list[str], bool]`,
    `walk(...)`, `walk_paths(...)`, `read(source, path, limit) -> bytes`,
    `write(source, path, data) -> None`. `Areas`, `AREAS`, `WRITABLE_AREA` and
    `ensure_folder` are removed.
- Produces, in `rag.workspace`: `Workspace(data_root: DataRoot, conninfo, encryption_key)`,
  attribute `Workspace.data_root`.
- Produces, over HTTP: the `GET folders`, `POST …/paths`, `GET file`, `PUT file` and
  `GET|PUT settings` behaviour of the Global Constraints. Task 8 consumes
  `data_root` in the settings response.

- [ ] **Step 1: Rename mechanically, then write the failing tests**

Run (from the repository root):
```bash
cd services/rag
perl -pi -e 's/rag-data/jira/g; s/\.areas\b/.data_root/g' tests/test_sync.py tests/test_file_log.py \
  tests/test_polling.py tests/test_collection_search.py tests/test_collections_api.py tests/test_paths_api.py \
  tests/test_workspace.py
perl -pi -e 's/rag-data/jira/g; s/\bareas\b/data_root/g' tests/test_collections.py
perl -pi -e 's/\.areas\b/.data_root/g' src/rag/workspace_worker.py src/rag/workspace_poll.py src/rag/workspace_api.py
```

Then make these edits.

`tests/conftest.py`: replace `from rag.collections import AREAS, Areas` with
`from rag.collections import DataRoot`, add `FOLDERS = ("code", "code-index", "docs", "generated-documents", "jira")`
below the imports, and in `env` replace the `roots = …` / `workspace = Workspace(…)` lines with:

```python
    data = tmp_path / "data"
    roots = {name: data / name for name in FOLDERS}
    for root in roots.values():
        root.mkdir(parents=True)
    roots["data"] = data
    workspace = Workspace(DataRoot(data, "/srv/data", "jira"), workspace_postgres + f" dbname={database}", b"k" * 32)
```

and the fixture docstring with
`"""(workspace, client, project, prefix, model_state, transport, roots); roots maps each default folder and "data" (DATA_ROOT) to its path."""`.

`tests/test_embedding_migration.py`: `from rag.collections import DataRoot` and
`Workspace(DataRoot(None), …)`.

`tests/test_workspace.py`:
- delete `from rag.collections import AREAS`;
- `collection()` becomes:

```python
def collection(env, name="docs", *sources, **settings):
    """Create collection `name` with `settings` and connect `sources` (default jira/<name>), creating the
    folders first since Add folder never does; returns its id."""
    collection_id = create(env, name, **settings)["id"]
    for source in sources or (f"jira/{name}",):
        (env[6]["data"] / source).mkdir(parents=True, exist_ok=True)
        added = add_folder(env, collection_id, source)
        assert added.status_code == 200, added.text
    return collection_id
```

- `write()`'s docstring becomes `"""Create or replace jira/<name>/<path> through the Jira writer."""`;
- replace `test_settings_report_areas_and_keep_only_the_file_limit` and
  `test_areas_come_from_deployment_environment` with:

```python
def test_settings_report_the_data_root_and_keep_only_the_file_limit(env):
    client, prefix = env[1], env[3]
    config = client.get(prefix + "/settings").json()
    assert config["settings"] == {"max_file_bytes": 2_000_000}
    assert "source_location" not in config and "source_root" not in config and "areas" not in config
    assert config["data_root"] == {"container_root": str(env[6]["data"]), "host_root": "/srv/data", "jira_folder": "jira"}
    payload = {"revision": config["revision"], "settings": {"max_file_bytes": 5000}}
    assert client.put(prefix + "/settings", json={**payload, "areas": []}).status_code == 400
    for legacy in ({"chunk_size": 1000}, {"extensions": [".md"]}, {"embedding_enabled": True}):
        assert client.put(prefix + "/settings", json={**payload, "settings": {"max_file_bytes": 5000, **legacy}}).status_code == 400
    saved = client.put(prefix + "/settings", json=payload)
    assert saved.status_code == 200 and saved.json()["settings"] == {"max_file_bytes": 5000}
    assert saved.json()["revision"] > config["revision"] and saved.json()["data_root"] == config["data_root"]


def test_data_root_comes_from_deployment_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", base64.urlsafe_b64encode(b"k" * 32).decode())
    monkeypatch.setenv("DATA_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("DATA_ROOT", "/srv/knowledge data/")
    monkeypatch.setenv("JIRA_EXPORT_ROOT", str(tmp_path / "exports" / "jira"))
    data_root = Workspace.from_env().data_root
    assert (data_root.root, data_root.jira_folder) == (tmp_path, "exports/jira")
    assert data_root.container_path("code/payments") == tmp_path / "code" / "payments"
    assert data_root.host_path("code/payments") == "/srv/knowledge data/code/payments"
    assert data_root.writable("exports/jira") and data_root.writable("exports/jira/2026")
    assert not data_root.writable("exports") and not data_root.writable("exports/jira2")
    monkeypatch.delenv("DATA_ROOT")
    assert Workspace.from_env().data_root.host_path("code/payments") is None
```

`tests/test_cli.py`, in `test_workspace_startup_has_no_legacy_indexes_or_initialization`:
replace the `RAG_SOURCE_ROOT` and `RG_DATA` lines with

```python
    monkeypatch.setenv("DATA_ROOT_DIR", str(source))
    monkeypatch.setenv("DATA_ROOT", str(source))
    monkeypatch.setenv("JIRA_EXPORT_ROOT", str(source / "jira"))
```

`tests/test_collections_api.py`: change both comments
`# only existing folders outside jira can be added` to `# Add folder never creates folders`.

`tests/test_collections.py`:
- imports: `from rag.collections import (DataRoot, FolderMissing, glob_regex, indexable, matches, overlaps, relative_path, source_path, visible)`
  and add `FOLDERS = ('code', 'code-index', 'docs', 'generated-documents', 'jira')`;
- the fixture:

```python
@pytest.fixture
def data_root(tmp_path):
    base = tmp_path / 'data'
    roots = {name: base / name for name in FOLDERS}
    for root in roots.values():
        root.mkdir(parents=True)
    roots['data'] = base
    return DataRoot(base, '/srv/data', 'jira'), roots
```

- the source path tests:

```python
@pytest.mark.parametrize('value', ['jira', 'code/payments/src', 'generated-documents', 'docs', 'exports/tools',
                                   'any-folder/v1.2', 'rag-data/demo'])
def test_valid_source_paths(value):
    assert source_path(value) == value


@pytest.mark.parametrize('value', ['', 'jira/../code', 'jira/.hidden', '/jira', 'jira//x', 'code/a b', '.hidden',
                                   '..', 'code/', 'a' * 501])
def test_invalid_source_paths(value):
    with pytest.raises(HTTPException):
        source_path(value)
```

- in `test_walk_filters_skips_and_reports_newest_change`, the last three lines become:

```python
    assert data_root.container_path('code/payments') == roots['code'] / 'payments'
    assert data_root.host_path('code/payments') == '/srv/data/code/payments'
    assert not data_root.writable('code/payments') and data_root.writable('jira') and not data_root.writable('jira2')
```

- replace `test_read_write_confined_to_rag_data` with:

```python
def test_read_write_confined_to_the_jira_folder(data_root):
    data_root, roots = data_root
    (roots['jira'] / 'notes').mkdir()
    data_root.write('jira/notes', 'sub/a.md', b'# A')
    assert data_root.read('jira/notes', 'sub/a.md', 100) == b'# A'
    with pytest.raises(HTTPException, match='size limit'):
        data_root.read('jira/notes', 'sub/a.md', 1)
    (roots['code'] / 'repo').mkdir()
    with pytest.raises(HTTPException, match='read-only') as error:
        data_root.write('code/repo', 'a.md', b'x')
    assert error.value.status_code == 403
    os.symlink(roots['code'], roots['jira'] / 'notes' / 'escape')
    with pytest.raises(OSError):
        data_root.read('jira/notes', 'escape/repo', 100)
```

- replace `test_present_and_ensure_folder` and `test_first_write_creates_the_collection_folder` with:

```python
def test_present_never_creates_folders(data_root):
    data_root, roots = data_root
    assert not data_root.present('jira/new') and not (roots['jira'] / 'new').exists()
    (roots['jira'] / 'new').mkdir()
    assert data_root.present('jira/new')
    os.symlink(roots['code'], roots['jira'] / 'link')
    assert not data_root.present('jira/link')


def test_first_write_creates_subfolders_only_below_the_jira_folder(data_root):
    data_root, roots = data_root
    data_root.write('jira', '2026/DEMO-1.md', b'# DEMO-1')
    assert (roots['jira'] / '2026' / 'DEMO-1.md').read_bytes() == b'# DEMO-1'
    with pytest.raises(HTTPException) as error:
        data_root.write('jira/absent', 'a.md', b'x')
    assert error.value.status_code == 409 and not (roots['jira'] / 'absent').exists()


def test_top_level_listing_shows_visible_folders_of_the_data_root(data_root):
    data_root, roots = data_root
    (roots['data'] / 'exports').mkdir()
    (roots['data'] / '.local-archive').mkdir()
    (roots['data'] / 'notes.md').write_text('x')
    os.symlink(roots['code'], roots['data'] / 'linked')
    assert data_root.subfolders(None) == (['code', 'code-index', 'docs', 'exports', 'generated-documents', 'jira'], False)


def test_reads_go_through_the_one_data_root_and_never_follow_links(data_root, tmp_path):
    data_root, roots = data_root
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    (elsewhere / 'a.md').write_text('outside-secret')
    os.symlink(elsewhere, roots['data'] / 'outside')
    assert not data_root.present('outside')
    with pytest.raises(HTTPException) as error:
        data_root.walk('outside', ['**/*.md'], [])
    assert error.value.status_code == 409
    with pytest.raises(OSError):
        data_root.read('outside', 'a.md', 100)
    for bad in ('../elsewhere', '.local-archive', 'code/.git'):
        with pytest.raises(HTTPException):
            data_root.container_path(bad)
    with pytest.raises(HTTPException) as unmounted:
        DataRoot(None).read('code', 'a.md', 100)
    assert unmounted.value.status_code == 503
```

`tests/test_paths_api.py`:
- replace `test_add_folder_creates_rag_data_folders_and_bumps_revision` with:

```python
def test_add_folder_accepts_any_existing_top_level_folder_and_bumps_revision(env):
    roots = env[6]
    (roots["code"] / "payments").mkdir()
    (roots["data"] / "exports" / "tools").mkdir(parents=True)
    row = create(env, "payments")
    added = add_folder(env, row["id"], "code/payments")
    assert added.status_code == 200, added.text
    assert folders(added.json()) == ["code/payments"] and added.json()["revision"] == row["revision"] + 1
    path = added.json()["paths"][0]
    assert path["host_path"] == "/srv/data/code/payments" and path["present"] is True and path["files"] == 0
    assert path["container_path"] == str(roots["code"] / "payments")
    assert folders(add_folder(env, row["id"], "exports/tools").json()) == ["code/payments", "exports/tools"]
    missing = add_folder(env, row["id"], "jira/tickets")
    assert missing.status_code == 409 and missing.json()["error"]["message"] == "Folder does not exist"
    assert not (roots["jira"] / "tickets").exists()  # never created, not even in the Jira folder
    assert add_folder(env, uuid4(), "code/payments").status_code == 404
```

- replace `test_add_folder_rejects_overlap_missing_and_bad_paths_and_changes_nothing` with:

```python
def test_add_folder_rejects_overlap_missing_and_bad_paths_and_changes_nothing(env, tmp_path):
    roots = env[6]
    (roots["code"] / "payments" / "src").mkdir(parents=True)
    os.symlink(roots["code"] / "payments", roots["code"] / "linked")
    (tmp_path / "elsewhere").mkdir()
    os.symlink(tmp_path / "elsewhere", roots["data"] / "outside")
    (roots["data"] / ".local-archive").mkdir()
    row = add_folder(env, create(env, "payments")["id"], "code/payments").json()
    for source in ("code/payments", "code/payments/src", "code"):
        refused = add_folder(env, row["id"], source)
        assert refused.status_code == 409, source
        assert refused.json()["error"]["message"] == "Folder overlaps code/payments in collection payments"
    for source in ("docs/absent", "code/linked", "outside", "tmp/x"):
        missing = add_folder(env, row["id"], source)
        assert missing.status_code == 409 and missing.json()["error"]["message"] == "Folder does not exist", source
    assert not (roots["docs"] / "absent").exists() and not (roots["data"] / "tmp").exists()
    for source in ("../etc", "code/payments/", "/code/payments", "code/a b", "code/.git", "code/../docs", "",
                   ".local-archive", "node_modules", "code/node_modules"):
        assert add_folder(env, row["id"], source).status_code == 400, source
    owned = env[1].post(env[3] + f"/collections/{row['id']}/paths", json={"source_path": "docs", "owner_kind": "jira"})
    assert owned.status_code == 400
    after = status(env, row["id"])
    assert folders(after) == ["code/payments"] and after["revision"] == row["revision"]
    other = create(env, "other")
    assert folders(add_folder(env, other["id"], "code/payments/src").json()) == ["code/payments/src"]  # collections may share
```

- replace `test_folder_browser_lists_visible_subfolders_and_rejects_bad_paths` with:

```python
def test_folder_browser_lists_the_data_root_and_rejects_bad_paths(env):
    client, prefix, roots = env[1], env[3], env[6]
    for name in ("payments", "billing", ".git", ".hidden", "node_modules", "with space"):
        (roots["code"] / name).mkdir()
    (roots["code"] / "README.md").write_text("x")
    os.symlink(roots["code"] / "payments", roots["code"] / "link")
    (roots["data"] / "exports").mkdir()
    (roots["data"] / ".local-archive").mkdir()
    (roots["data"] / "startup.log").write_text("x")
    os.symlink(roots["code"], roots["data"] / "linked")
    top = client.get(prefix + "/folders").json()
    assert top == {"path": None, "truncated": False, "folders": [
        {"name": name, "path": name} for name in ("code", "code-index", "docs", "exports", "generated-documents", "jira")]}
    code = client.get(prefix + "/folders", params={"path": "code"}).json()
    assert code == {"path": "code", "truncated": False, "folders": [
        {"name": "billing", "path": "code/billing"}, {"name": "payments", "path": "code/payments"}]}
    for bad in ("../etc", "code/../docs", "/code", "code/.git", "code/", ".local-archive"):
        assert client.get(prefix + "/folders", params={"path": bad}).status_code == 400, bad
    for missing in ("code/absent", "tmp", "linked"):
        response = client.get(prefix + "/folders", params={"path": missing})
        assert response.status_code == 404 and response.json()["error"]["message"] == "Folder does not exist", missing
```

- replace `test_rag_data_files_are_read_by_full_path_without_a_collection` with:

```python
def test_jira_files_are_read_by_full_path_without_a_collection(env):
    client, prefix, roots = env[1], env[3], env[6]
    (roots["jira"] / "DEMO-1.md").write_text("# DEMO-1")
    (roots["jira"] / "2026").mkdir()
    (roots["jira"] / "2026" / "DEMO-2.md").write_text("# DEMO-2")
    (roots["jira"] / ".env").write_text("SECRET=1")
    (roots["data"] / "jira2").mkdir()
    (roots["data"] / "jira2" / "x.md").write_text("look-alike")
    (roots["code"] / "x.md").write_text("code")
    found = client.get(prefix + "/file", params={"path": "jira/DEMO-1.md"})
    assert found.status_code == 200 and found.json()["content"] == "# DEMO-1" and found.json()["collection"] is None
    assert client.get(prefix + "/file", params={"path": "jira/2026/DEMO-2.md"}).json()["content"] == "# DEMO-2"
    for path in ("jira/.env", "code/x.md", "jira/absent.md", "jira", "jira2/x.md"):
        response = client.get(prefix + "/file", params={"path": path})
        assert response.status_code == 404, path
        assert "SECRET" not in response.text and "look-alike" not in response.text
```

- replace `test_rag_data_writer_uses_full_paths` with:

```python
def test_jira_writer_writes_only_inside_the_jira_folder(env):
    roots = env[6]
    client, prefix = env[1], env[3]
    put = lambda path, content="# A", expected=None: client.put(prefix + "/file", json={"path": path, "content": content, "expected_hash": expected})
    saved = put("jira/DEMO-1.md", "# DEMO-1")
    assert saved.status_code == 200 and saved.json()["path"] == "jira/DEMO-1.md"
    assert (roots["jira"] / "DEMO-1.md").read_text() == "# DEMO-1"
    assert put("jira/DEMO-1.md", "# stale").status_code == 409
    assert put("jira/DEMO-1.md", "# DEMO-1 v2", saved.json()["hash"]).status_code == 200
    assert put("jira/2026/DEMO-2.md").status_code == 200 and (roots["jira"] / "2026" / "DEMO-2.md").is_file()
    for path in ("code/payments/a.md", "jira", "jira2/a.md", "docs/a.md", "exports/a.md"):
        refused = put(path)
        assert refused.status_code == 403, path
        assert refused.json()["error"]["message"] == "Only files inside jira can be written; other folders are read-only"
    for path in ("jira/.env", "jira/.git/config", "jira/node_modules/a.md", "../x.md"):
        assert put(path).status_code == 400, path
    assert not (roots["code"] / "payments").exists() and not (roots["data"] / "jira2").exists()
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/rag && uv run --frozen pytest -q`
Expected: FAIL with `ImportError: cannot import name 'DataRoot' from 'rag.collections'`.

- [ ] **Step 3: Replace the area code in `services/rag/src/rag/collections.py`**

Change the module docstring to `"""Collection folders under DATA_ROOT: one confined root, glob selection and walks."""`
and add `from pathlib import Path, PurePosixPath` (replacing `from pathlib import Path`).

Replace `AREAS`, `WRITABLE_AREA`, `SOURCE_PATH` and `FOLDER_NAME` with:

```python
SEGMENT = r'[A-Za-z0-9_-][A-Za-z0-9._-]*'  # a visible folder name: no leading '.', so no hidden folders or '..'
SOURCE_PATH = re.compile(rf'{SEGMENT}(/{SEGMENT})*')
FOLDER_NAME = re.compile(SEGMENT)  # a name that can be a source_path segment
```

(`SKIPPED_DIRECTORIES`, `MAX_FILES`, `MAX_SYNC_ENTRIES`, `MAX_BYTES` and
`MAX_FOLDERS` stay.)

Replace `source_path`:

```python
def source_path(value: str) -> str:
    if len(value) > 500 or not SOURCE_PATH.fullmatch(value):
        raise HTTPException(400, 'Choose a folder inside DATA_ROOT: names of letters, digits, ".", "_" and "-" '
                                 'separated by "/", none starting with "."')
    return value
```

Replace the whole `class Areas` with:

```python
class DataRoot:
    """DATA_ROOT inside the container: every collection folder lives under this one root.

    Every access opens the root and walks down one segment at a time with O_NOFOLLOW, so no link is
    followed and nothing outside the root is reached. RAG writes only inside `jira_folder`.
    """

    def __init__(self, root: Path | None, host_root: str | None = None, jira_folder: str | None = None):
        self.root = Path(root) if root else None
        self.host_root = host_root
        self.jira_folder = jira_folder

    @classmethod
    def from_env(cls):
        """DATA_ROOT_DIR (container), DATA_ROOT (host, display only) and the Jira folder from JIRA_EXPORT_ROOT."""
        container = os.environ.get('DATA_ROOT_DIR')
        jira = os.environ.get('JIRA_EXPORT_ROOT')
        folder = source_path(PurePosixPath(jira).relative_to(container).as_posix()) if container and jira else None
        return cls(Path(container) if container else None, os.environ.get('DATA_ROOT') or None, folder)

    def _root(self) -> Path:
        if self.root is None:
            raise HTTPException(503, 'DATA_ROOT is not mounted in the RAG service')
        return self.root

    def container_path(self, source: str) -> Path:
        return self._root().joinpath(*source_path(source).split('/'))

    def host_path(self, source: str) -> str | None:
        return None if not self.host_root else self.host_root.rstrip('/') + '/' + source

    def writable(self, source: str) -> bool:
        """Whether `source` is the Jira folder or inside it: the only place RAG writes."""
        folder = self.jira_folder
        return folder is not None and (source == folder or source.startswith(folder + '/'))

    @contextmanager
    def _directory(self, source: str | None, parts: list[str], *, create: bool = False):
        """A descriptor of DATA_ROOT/<source>/<parts>; `source` None is DATA_ROOT itself.

        With `create`, missing `parts` are made (0o750); `source` itself must already exist.
        """
        root = self._root()
        segments = [] if source is None else source_path(source).split('/')
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for index, part in enumerate([*segments, *parts]):
                if create and index >= len(segments):
                    try:
                        os.mkdir(part, mode=0o750, dir_fd=fd)
                    except FileExistsError:
                        pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            yield fd
        finally:
            os.close(fd)

    def present(self, source: str) -> bool:
        """Whether the collection folder exists as a real directory (no walk, never created)."""
        try:
            with self._directory(source, []):
                return True
        except OSError:
            return False
```

Then move the existing `subfolders`, `walk`, `walk_paths`, `read` and `write` methods
into `DataRoot` unchanged except:
- `subfolders(self, source: str | None, limit: int = MAX_FOLDERS)`; its docstring adds
  "`source` None lists the top level of DATA_ROOT. A link is not a folder: 404 like a
  missing one."; its final `except` clause becomes:

```python
        except (FileNotFoundError, NotADirectoryError):
            raise HTTPException(404, 'Folder does not exist') from None
        except OSError as error:
            if error.errno == errno.ELOOP:
                raise HTTPException(404, 'Folder does not exist') from None
            raise
```

- `write` becomes:

```python
    def write(self, source: str, path: str, data: bytes) -> None:
        """Create or replace `path` inside `source`, which must be the Jira folder or inside it."""
        if not self.writable(source):
            raise HTTPException(403, f'Only files inside {self.jira_folder or "the Jira folder"} can be written; '
                                     'other folders are read-only')
        *parents, name = relative_path(path).split('/')
        refuse_hidden(path)
        try:
            with self._directory(source, parents, create=True) as fd:
                temporary = f'.upload-{uuid4().hex}'
                try:
                    leaf = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640, dir_fd=fd)
                    with os.fdopen(leaf, 'wb') as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
                finally:
                    try:
                        os.unlink(temporary, dir_fd=fd)
                    except FileNotFoundError:
                        pass
        except FileNotFoundError:
            raise HTTPException(409, f'Folder {source} does not exist; run make setup to create it') from None
```

Delete `ensure_folder` and the static `writable`.

- [ ] **Step 4: `Workspace.data_root` in `services/rag/src/rag/workspace.py`**

`from rag.collections import DataRoot`; the constructor becomes
`def __init__(self, data_root: DataRoot, conninfo: str, encryption_key: bytes):` with
`self.data_root = data_root`; `from_env` returns
`cls(DataRoot.from_env(), "", base64.urlsafe_b64decode(os.environ["CREDENTIAL_ENCRYPTION_KEY"]))`.

- [ ] **Step 5: The API in `services/rag/src/rag/workspace_api.py`**

- Module docstring first line: `"""Authenticated backoffice API; DATA_ROOT comes from the deployment, never from input.`
- Import: `from rag.collections import (FolderMissing, indexable, overlaps, refuse_hidden, relative_path, source_path, too_large, visible)`.
- `FileInput` docstring: `"""A file inside the Jira folder, addressed by its full DATA_ROOT path (the Jira export writes here)."""`
- `config_response`:

```python
    def config_response(row):
        data_root = workspace.data_root
        return {"revision": row["revision"], "settings": row["settings"],
                "embedding_model_id": row["embedding_model_id"],
                "data_root": {"container_root": str(data_root.root) if data_root.root else None,
                              "host_root": data_root.host_root, "jira_folder": data_root.jira_folder}}
```

- `add_folder`: docstring `"""Connect an existing folder under DATA_ROOT; RAG never creates folders."""`
  and the presence check becomes:

```python
            if not workspace.data_root.present(new):
                raise HTTPException(409, "Folder does not exist")
```

- `folders`:

```python
    @router.get(prefix + "/folders")
    def folders(project: UUID, path: str | None = Query(None, max_length=500)):
        """Subfolders of a DATA_ROOT folder for the folder browser; without `path`, the top level of DATA_ROOT."""
        names, truncated = workspace.data_root.subfolders(None if path is None else folder_path(path))
        return {"path": path, "truncated": truncated,
                "folders": [{"name": name, "path": f"{path}/{name}" if path else name} for name in names]}
```

- `get_file`:

```python
    @router.get(prefix + "/file")
    def get_file(project: UUID, path: str, collection: str | None = None):
        """An indexable file of `collection` by its full path. Without `collection`, a visible file inside the
        Jira folder (the Jira export reads its previous ticket file this way)."""
        path = relative_path(path)
        jira = workspace.data_root.jira_folder
        with workspace.connect() as conn:
            limit = Settings(**workspace.settings(conn, project)["settings"]).max_file_bytes
            if collection is None:
                allowed = jira is not None and path.startswith(jira + "/") and visible(path[len(jira) + 1:])
            else:
                row = workspace.collection_by_name(conn, project, collection)
                roots = [p["source_path"] for p in workspace.paths(conn, row["id"])]
                root = next((r for r in roots if path.startswith(r + "/")), None)
                # Only what sync would index: under one of the collection's folders, never .env*, hidden or
                # skipped folders, or files its patterns (matched relative to that folder) leave out.
                allowed = root is not None and indexable(path[len(root) + 1:], row["include"], row["exclude"])
        if not allowed:
            raise HTTPException(404, "Document not found")
        first, _, rest = path.partition("/")
        try:
            data = workspace.data_root.read(first, rest, limit)
        except FileNotFoundError:
            raise HTTPException(404, "Document not found") from None
        try:
            content = data.decode("utf-8-sig")
        except UnicodeError:
            raise HTTPException(400, "File is not UTF-8 text") from None
        return {"collection": collection, "path": path, "content": content, "hash": digest(data)}
```

- `put_file`:

```python
    @router.put(prefix + "/file")
    def put_file(project: UUID, payload: FileInput):
        """Create or replace a file inside the Jira folder, the one folder RAG writes."""
        path = relative_path(payload.path)
        jira = workspace.data_root.jira_folder
        if jira is None or not path.startswith(jira + "/"):
            raise HTTPException(403, f"Only files inside {jira or 'the Jira folder'} can be written; other folders are read-only")
        rest = path[len(jira) + 1:]
        refuse_hidden(rest)
        with workspace.connect() as conn:
            limit = Settings(**workspace.settings(conn, project)["settings"]).max_file_bytes
        data = payload.content.encode("utf-8")
        if len(data) > limit:
            raise HTTPException(400, "File exceeds the configured size limit")
        try:
            previous = digest(workspace.data_root.read(jira, rest, limit))
        except FileNotFoundError:
            previous = None
        if previous != payload.expected_hash:
            raise HTTPException(409, "File already exists or has changed; open it before replacing it")
        workspace.data_root.write(jira, rest, data)
        return {"path": path, "hash": digest(data)}
```

`workspace_worker.read` keeps `area, _, rest = path.partition("/")`; rename the local
to `first` for clarity.

- [ ] **Step 6: Run the tests and confirm they pass**

Run: `cd services/rag && uv run --frozen pytest -q`
Expected: PASS. Then
`git grep -n "AREAS\|Areas\|WRITABLE_AREA\|ensure_folder\|\.areas\b\|rag-data" -- services/rag/src services/rag/tests`
prints only `services/rag/tests/test_collections.py` (`'rag-data/demo'`, a valid
ordinary folder name in `test_valid_source_paths`).

- [ ] **Step 7: Commit**

```bash
git add services/rag
git commit -m "feat(rag): one DATA_ROOT, any top-level folder, writes only inside the Jira folder

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: admin-api — `data_root` module, agent read roots and the shell worker root

**Files:**
- Create: `services/admin-api/src/knowledge_admin/data_root.py`
- Create: `services/admin-api/tests/test_data_root.py`
- Modify: `services/admin-api/src/knowledge_admin/collections.py`
- Modify: `services/admin-api/src/knowledge_admin/agents.py`
- Modify: `services/admin-api/src/knowledge_admin/shell_worker.py`
- Modify: `services/admin-api/src/knowledge_admin/tools/documents.py` (docstring example)
- Modify: `services/admin-api/tests/test_agent_collections.py`, `test_shell_worker.py`,
  `test_agent_wizard.py`, `test_reader_settings.py`, `test_board.py`, `test_profiles.py`

**Interfaces:**
- Consumes: `DATA_ROOT_DIR`, `DATA_ROOT`, `JIRA_EXPORT_ROOT` (Task 2).
- Produces, in `knowledge_admin.data_root`: `data_dir() -> PurePosixPath`,
  `data_path(source_path: str) -> PurePosixPath`,
  `host_path(container_path: str | None) -> str | None`, `jira_folder() -> str`.
  Tasks 6 and 7 use `host_path` and `jira_folder`.
- Removes: `knowledge_admin.collections.AREA_ROOTS` and `area_root`.

- [ ] **Step 1: Write the failing tests**

Create `services/admin-api/tests/test_data_root.py`:

```python
import pytest

from knowledge_admin.data_root import data_dir, data_path, host_path, jira_folder


def test_paths_follow_the_container_data_root(monkeypatch):
    monkeypatch.setenv('DATA_ROOT_DIR', '/work/data')
    monkeypatch.setenv('DATA_ROOT', '/opt/knowledge agent/')
    monkeypatch.setenv('JIRA_EXPORT_ROOT', '/work/data/exports/jira')
    assert str(data_path('exports/tools')) == '/work/data/exports/tools'
    assert host_path('/work/data') == '/opt/knowledge agent'
    assert host_path('/work/data/code') == '/opt/knowledge agent/code'
    assert host_path('/elsewhere/code') is None and host_path(None) is None
    assert jira_folder() == 'exports/jira'
    monkeypatch.delenv('DATA_ROOT')
    assert host_path('/work/data/code') is None


def test_defaults_and_misconfiguration(monkeypatch):
    for name in ('DATA_ROOT_DIR', 'JIRA_EXPORT_ROOT', 'DATA_ROOT'):
        monkeypatch.delenv(name, raising=False)
    assert str(data_dir()) == '/work/data' and jira_folder() == 'jira'
    monkeypatch.setenv('DATA_ROOT_DIR', 'relative')
    with pytest.raises(ValueError, match='DATA_ROOT_DIR'):
        data_dir()
    monkeypatch.setenv('DATA_ROOT_DIR', '/work/data')
    for outside in ('/work/other/jira', '/work/data'):
        monkeypatch.setenv('JIRA_EXPORT_ROOT', outside)
        with pytest.raises(ValueError, match='JIRA_EXPORT_ROOT'):
            jira_folder()
```

In `services/admin-api/tests/test_agent_collections.py`:
- line 2 becomes `from knowledge_admin.collections import document_collections, slug`,
  and add `import pytest` as the first import;
- in `test_agent_config_lists_granted_collections_with_paths`, replace the three
  `monkeypatch.setenv` lines with
  `monkeypatch.setenv('DATA_ROOT_DIR', '/work/data')` and
  `monkeypatch.setenv('CODE_ROOT', '/work/data/code')`, the seed with
  `seed(store, ('jira', 'jira'), ('payments', 'docs/payments', 'code/payments'))`, and
  the expected paths with `['/work/data/code/payments', '/work/data/docs/payments']`
  and `[('jira', ['/work/data/jira'])]`;
- replace `test_area_root_maps_every_area_to_its_container_root` with:

```python
def test_agent_config_maps_any_folder_under_the_data_root(store, monkeypatch):
    monkeypatch.setenv('DATA_ROOT_DIR', '/work/data')
    monkeypatch.setenv('CODE_ROOT', '/work/data/code')
    seed(store, ('jira', 'jira'), ('tools', 'exports/tools', 'code-index/abc'))
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET all_collections=true,documents_enabled=true WHERE id='search'")
    config = get_agent_config('search', conninfo=store.conninfo)
    assert [(c['name'], c['paths']) for c in config['documents']['collections']] == [
        ('jira', ['/work/data/jira']), ('tools', ['/work/data/code-index/abc', '/work/data/exports/tools'])]
    monkeypatch.setenv('DATA_ROOT_DIR', 'relative')
    with pytest.raises(ValueError, match='DATA_ROOT_DIR'):
        get_agent_config('search', conninfo=store.conninfo)
```

In `test_agent_wizard.py`, `test_reader_settings.py`, `test_board.py` and
`test_profiles.py`, replace `monkeypatch.setenv('RAG_SOURCE_ROOT', …)` with
`monkeypatch.setenv('DATA_ROOT_DIR', …)` (same value). In `test_shell_worker.py`,
`test_configured_source_mount_is_allowed_without_exposing_other_roots` sets
`monkeypatch.setenv('DATA_ROOT_DIR', directory)` instead of `CODE_ROOT`.

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_data_root.py tests/test_agent_collections.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'knowledge_admin.data_root'`.

- [ ] **Step 3: Create `services/admin-api/src/knowledge_admin/data_root.py`**

```python
"""DATA_ROOT inside the containers (DATA_ROOT_DIR) and on the host (DATA_ROOT, display only)."""
import os
from pathlib import PurePosixPath

DEFAULT_DIR = '/work/data'


def data_dir() -> PurePosixPath:
    root = PurePosixPath(os.environ.get('DATA_ROOT_DIR') or DEFAULT_DIR)
    if root.anchor != '/' or '..' in root.parts:
        raise ValueError('DATA_ROOT_DIR must be an absolute path mounted in the container')
    return root


def data_path(source_path: str) -> PurePosixPath:
    """A RAG collection folder (relative to DATA_ROOT) as a container path."""
    return data_dir() / source_path


def _relative(container_path: str) -> str | None:
    """The path relative to DATA_ROOT_DIR ('' for the root itself), or None outside it."""
    path, root = PurePosixPath(container_path), data_dir()
    if path == root:
        return ''
    return path.relative_to(root).as_posix() if path.is_relative_to(root) else None


def host_path(container_path: str | None) -> str | None:
    """Where a container path under DATA_ROOT_DIR lives on the host; None when unknown."""
    host = (os.environ.get('DATA_ROOT') or '').rstrip('/')
    relative = _relative(container_path) if container_path else None
    if not host or relative is None:
        return None
    return host + ('/' + relative if relative else '')


def jira_folder() -> str:
    """JIRA_FOLDER: where the Jira export writes, relative to DATA_ROOT (the RAG writer's path prefix)."""
    relative = _relative(os.environ.get('JIRA_EXPORT_ROOT') or f'{data_dir()}/jira')
    if not relative:
        raise ValueError('JIRA_EXPORT_ROOT must be a folder inside DATA_ROOT_DIR')
    return relative
```

- [ ] **Step 4: Use it**

`services/admin-api/src/knowledge_admin/collections.py`:
- delete `import os`, `AREA_ROOTS` and `area_root`;
- add `LEGACY_RAG_ROOT = PurePosixPath('/work/rag-data')  # where folder-era configs (before 0063) kept folders`;
- in `document_collections`, replace
  `rag_root = PurePosixPath(os.environ.get('RAG_SOURCE_ROOT', '/work/rag-data'))` with
  `rag_root = LEGACY_RAG_ROOT` and the comment `below RAG_SOURCE_ROOT` with
  `below the old rag-data mount`.

`services/admin-api/src/knowledge_admin/agents.py`:
- import `from knowledge_admin.collections import document_collections` and
  `from knowledge_admin.data_root import data_path`;
- `item['paths'] = [str(data_path(path)) for path in item.pop('source_paths')]`;
- in the `get_agent_config` docstring replace the two lines about `CODE_ROOT and the roots of the areas…`
  with `CODE_ROOT and DATA_ROOT_DIR (where every collection folder lives) must match absolute mounts in the agent's container. Paths are joined without resolving them on the loader's host.`

`services/admin-api/src/knowledge_admin/shell_worker.py`: replace the `roots = […]`
statement with

```python
    roots = [Path(os.environ.get('DATA_ROOT_DIR', '/work/data'))]  # every source folder lives under DATA_ROOT
```

`services/admin-api/src/knowledge_admin/tools/documents.py`: in the `read_document`
docstring the example becomes `"jira/DEMO-1.md"`.

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `cd services/admin-api && uv run --frozen pytest -q`
Expected: PASS (the bubblewrap shell tests skip where they skip today).

- [ ] **Step 6: Commit**

```bash
git add services/admin-api
git commit -m "feat(admin-api): agent and shell read roots under DATA_ROOT_DIR

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: admin-api — storage overview of `DATA_ROOT` and its folders, code browser host path

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/storage.py`
- Modify: `services/admin-api/src/knowledge_admin/repository_api.py` (`list_code_files`)
- Modify: `services/admin-api/tests/test_storage.py`, `test_code_projects.py`

**Interfaces:**
- Consumes: `knowledge_admin.data_root.host_path` (Task 5).
- Produces: `GET /api/storage` → `{locations: [{id, container_path, host_path, variable, status}]}`
  for `data`, `code`, `code_index`, `jira`, `documents`, `bootstrap`;
  `GET /api/storage/{area}/entries|file` accept the same ids. `GET /api/code/files`
  `host_path` is `DATA_ROOT/<CODE_FOLDER>`. Task 8 consumes the storage shape.

- [ ] **Step 1: Write the failing tests**

`services/admin-api/tests/test_storage.py`: replace the fixture and the first test with:

```python
@pytest.fixture
def storage(tmp_path, monkeypatch):
    data = tmp_path / 'data'
    root = data / 'code'
    (root / 'billing').mkdir(parents=True)
    (root / 'billing' / 'README.md').write_text('# Billing\n', encoding='utf-8')
    monkeypatch.setenv('DATA_ROOT_DIR', str(data))
    monkeypatch.setenv('DATA_ROOT', '/opt/knowledge-agent')
    monkeypatch.setenv('CODE_ROOT', str(root))
    monkeypatch.setenv('JIRA_EXPORT_ROOT', str(data / 'jira'))  # not created: unavailable
    for name in ('CODE_INDEX_ROOT', 'DOCUMENT_OUTPUT_ROOT', 'BOOTSTRAP_DOCS_ROOT'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('CREDENTIAL_ENCRYPTION_KEY', 'must-not-appear')
    return _client(tmp_path), root


def test_storage_overview_lists_the_data_root_and_its_folders_with_host_paths(storage):
    client, root = storage
    response = client.get('/api/storage', headers=HEADERS)
    assert response.status_code == 200
    locations = {value['id']: value for value in response.json()['locations']}
    assert list(locations) == ['data', 'code', 'code_index', 'jira', 'documents', 'bootstrap']
    assert locations['data'] == {'id': 'data', 'container_path': str(root.parent), 'host_path': '/opt/knowledge-agent',
                                 'variable': 'DATA_ROOT', 'status': 'available'}
    assert locations['code'] == {'id': 'code', 'container_path': str(root), 'host_path': '/opt/knowledge-agent/code',
                                 'variable': 'CODE_FOLDER', 'status': 'available'}
    assert (locations['jira']['host_path'], locations['jira']['variable'], locations['jira']['status']) == (
        '/opt/knowledge-agent/jira', 'JIRA_FOLDER', 'unavailable')
    assert locations['documents'] == {'id': 'documents', 'container_path': None, 'host_path': None,
                                      'variable': 'GENERATED_DOCS_FOLDER', 'status': 'unconfigured'}
    assert locations['bootstrap']['variable'] == 'DOCS_FOLDER' and locations['code_index']['variable'] == 'CODE_INDEX_FOLDER'
    assert 'must-not-appear' not in response.text
```

and append to `test_browse_nested_folders_and_preview_text_without_writing`:

```python
    top = client.get('/api/storage/data/entries', headers=HEADERS).json()
    assert [entry['path'] for entry in top['entries']] == ['code']
    assert client.get('/api/storage/rag/entries', headers=HEADERS).status_code == 422
```

`services/admin-api/tests/test_code_projects.py`, fixture `workspace`: replace the
`CODE_PATH` and `RAG_SOURCE_ROOT` lines with

```python
    monkeypatch.setenv('DATA_ROOT_DIR', str(tmp_path))
    monkeypatch.setenv('DATA_ROOT', '/opt/knowledge-agent')
```

and in `test_code_browser_lists_hidden_entries_reads_files_and_stays_inside_mount`
the root assertion becomes

```python
    assert listing['root'] == str(root) and listing['host_path'] == '/opt/knowledge-agent/code'
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_storage.py tests/test_code_projects.py -q`
Expected: FAIL. There is no `data` location, and `host_path` is `None`.

- [ ] **Step 3: Implement**

`services/admin-api/src/knowledge_admin/storage.py`:
- docstring `"""Read-only overview of DATA_ROOT and its configured folders."""`;
- import `from knowledge_admin.data_root import host_path`;
- replace `StorageArea` and `ROOTS` with:

```python
StorageArea = Literal['data', 'code', 'code_index', 'jira', 'documents', 'bootstrap']
# The in-container variable Compose computes, and the .env variable that names the folder.
ROOTS = {
    'data': ('DATA_ROOT_DIR', 'DATA_ROOT'),
    'code': ('CODE_ROOT', 'CODE_FOLDER'),
    'code_index': ('CODE_INDEX_ROOT', 'CODE_INDEX_FOLDER'),
    'jira': ('JIRA_EXPORT_ROOT', 'JIRA_FOLDER'),
    'documents': ('DOCUMENT_OUTPUT_ROOT', 'GENERATED_DOCS_FOLDER'),
    'bootstrap': ('BOOTSTRAP_DOCS_ROOT', 'DOCS_FOLDER'),
}
```

- `overview()` becomes:

```python
    @router.get('')
    def overview():
        locations = []
        for area, (container_variable, variable) in ROOTS.items():
            configured = os.environ.get(container_variable) or None
            available = bool(configured and Path(configured).is_absolute() and Path(configured).is_dir())
            locations.append({'id': area, 'container_path': configured, 'host_path': host_path(configured),
                              'variable': variable,
                              'status': 'available' if available else 'unavailable' if configured else 'unconfigured'})
        return {'locations': locations}
```

`services/admin-api/src/knowledge_admin/repository_api.py`: import
`from knowledge_admin.data_root import host_path` and in `list_code_files` return
`'host_path': host_path(str(root))` instead of `os.environ.get('CODE_PATH')`.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_storage.py tests/test_code_projects.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api
git commit -m "feat(admin-api): storage overview lists DATA_ROOT and its folders with host paths

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: admin-api — Jira export writes to `JIRA_EXPORT_ROOT` and the tickets response names the folder

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/jira_export.py`
- Modify: `services/admin-api/src/knowledge_admin/jira_api.py` (`list_tickets`)
- Modify: `services/admin-api/src/knowledge_admin/rag_files.py` (docstrings)
- Modify: `services/admin-api/tests/test_jira_export.py`

**Interfaces:**
- Consumes: `knowledge_admin.data_root.jira_folder` (Task 5); RAG's Jira writer (Task 4).
- Produces: export path `<jira_folder()>/<KEY>.md`; `GET /api/jira/tickets` and
  `POST /api/jira/tickets/fetch` → `{connection, tickets, export_folder}`. Task 9
  consumes `export_folder`.
- Removes: `jira_export.JIRA_PATH`.

- [ ] **Step 1: Write the failing tests**

In `services/admin-api/tests/test_jira_export.py`:
- run `perl -pi -e 's#rag-data/jira#jira#g' services/admin-api/tests/test_jira_export.py`;
- add `from knowledge_admin.data_root import jira_folder` to the imports;
- at the start of the `exporting` fixture add
  `monkeypatch.setenv("DATA_ROOT_DIR", "/work/data")` and
  `monkeypatch.setenv("JIRA_EXPORT_ROOT", "/work/data/jira")`;
- in the mock's PUT branch replace `body["path"].startswith("jira/")` with
  `body["path"].startswith(jira_folder() + "/")`;
- add:

```python
def test_export_writes_into_the_configured_jira_folder_and_the_list_names_it(exporting, monkeypatch):
    client, snapshot, state = exporting
    assert client.get("/api/jira/tickets", headers=HEADERS).json()["export_folder"] == "jira"
    monkeypatch.setenv("JIRA_EXPORT_ROOT", "/work/data/exports/jira")
    response = export(client, snapshot)
    assert response.status_code == 200, response.text
    assert response.json()["path"] == "exports/jira/DEMO-1.md" and list(state["files"]) == ["exports/jira/DEMO-1.md"]
    listing = client.get("/api/jira/tickets", headers=HEADERS).json()
    assert listing["export_folder"] == "exports/jira" and set(listing) == {"connection", "tickets", "export_folder"}
    fetched = client.post("/api/jira/tickets/fetch", headers=HEADERS, json={"limit": 3}).json()
    assert fetched["export_folder"] == "exports/jira"
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_jira_export.py -q`
Expected: FAIL. Exports still go to `rag-data/jira/…`, and the list has no `export_folder`.

- [ ] **Step 3: Implement**

`services/admin-api/src/knowledge_admin/jira_export.py`: delete `JIRA_PATH`, import
`from knowledge_admin.data_root import jira_folder`, and:

```python
def publish_ticket(client: httpx.Client, key: str, project: UUID, actor: UUID, issue_key: str, content: str) -> dict:
    """Write the ticket to <JIRA_FOLDER>/<KEY>.md; collections that hold that folder index it at their next sync."""
    return RagFiles(client, key, project, actor).write_markdown(f"{jira_folder()}/{issue_key}.md", content)
```

`services/admin-api/src/knowledge_admin/jira_api.py`: import
`from knowledge_admin.data_root import jira_folder`; `list_tickets` returns
`{"connection": config, "tickets": rows, "export_folder": jira_folder()}`.

`services/admin-api/src/knowledge_admin/rag_files.py`: the module docstring becomes
`"""Read and write files in the Jira folder through the RAG backoffice API."""`;
`get_file`: `"""A file in the Jira folder by its full DATA_ROOT path, or None when it does not exist."""`;
`write_markdown`: `"""Create or replace `path` (full, inside the Jira folder). A file whose first line differs belongs to another source and is kept."""`.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_jira_export.py tests/test_jira.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api
git commit -m "feat(jira): export to JIRA_EXPORT_ROOT and name the folder in the tickets response

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: admin-ui — storage overview, RAG Settings data folder, folder picker at the `DATA_ROOT` top level

**Files:**
- Modify: `services/admin-ui/app/lib/storage.ts`, `services/admin-ui/app/lib/rag.ts`
- Modify: `services/admin-ui/app/components/StorageOverviewView.tsx`,
  `RagSettingsView.tsx`, `RagFolderPicker.tsx`
- Modify: `services/admin-ui/app/components/__tests__/storageOverview.test.tsx`,
  `rag.test.tsx`, `ragFolderPicker.test.tsx`, `ragCollections.test.tsx`,
  `ragFileLog.test.tsx`

**Interfaces:**
- Consumes: the storage shape (Task 6) and RAG `data_root` (Task 4).
- Produces: `StorageArea = 'data' | 'code' | 'code_index' | 'jira' | 'documents' | 'bootstrap'`;
  `StorageLocation { id; container_path: string | null; host_path: string | null; variable: string; status }`;
  `RagDataRoot { container_root: string | null; host_root: string | null; jira_folder: string | null }`
  and `RagConfig.data_root` (replacing `areas` and `RagArea`).

- [ ] **Step 1: Write the failing tests**

`storageOverview.test.tsx`: replace `locations` and the first test with:

```tsx
const locations = [
  { id: 'data', container_path: '/work/data', host_path: '/opt/ka', variable: 'DATA_ROOT', status: 'available' },
  { id: 'code', container_path: '/work/data/code', host_path: '/opt/ka/code', variable: 'CODE_FOLDER', status: 'available' },
  { id: 'jira', container_path: '/work/data/jira', host_path: '/opt/ka/jira', variable: 'JIRA_FOLDER', status: 'available' },
  { id: 'documents', container_path: '/work/data/generated-documents', host_path: '/opt/ka/generated-documents', variable: 'GENERATED_DOCS_FOLDER', status: 'available' },
  { id: 'bootstrap', container_path: '/work/data/docs', host_path: null, variable: 'DOCS_FOLDER', status: 'available' },
];
```

```tsx
test('lists DATA_ROOT and its folders with host paths and .env variables', async () => {
  mockStorage();
  render(<MemoryRouter><StorageOverviewView /></MemoryRouter>);
  expect(await screen.findByText('/opt/ka')).toBeInTheDocument();
  expect(screen.getByText('Host · DATA_ROOT')).toBeInTheDocument();
  expect(screen.getByText('/opt/ka/jira')).toBeInTheDocument();
  expect(screen.getByText('Host · JIRA_FOLDER')).toBeInTheDocument();
  expect(screen.getByText('/work/data/generated-documents')).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'Browse Data folder' })).toHaveAttribute('href', '/storage?area=data&path=&offset=0#files');
  expect(screen.getByRole('link', { name: 'Export Jira tickets' })).toHaveAttribute('href', '/jira/tickets');
  expect(screen.getByText(/Jira exports to the Jira export folder/)).toBeInTheDocument();
  expect(screen.queryByText(/rag-data|Relative to the Compose file directory/)).toBeNull();
  expect(screen.getByText(/PostgreSQL stores managed documents and versions/)).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'Manage documents' })).toHaveAttribute('href', '/documents');
});
```

`rag.test.tsx`: the config becomes

```tsx
const config: RagConfig = { revision: 3, settings: { max_file_bytes: 2000000 }, embedding_model_id: null,
  data_root: { container_root: "/work/data", host_root: "/srv/knowledge", jira_folder: "jira" } };
```

and the first test is renamed `"settings show the data folder read-only and save only max file size and model selection"`
with `expect(screen.getByText("/srv/knowledge")).toBeInTheDocument();` and
`expect(screen.getByText("/work/data")).toBeInTheDocument();` in place of the two
area assertions, plus `expect(screen.getByRole("cell", { name: "jira" })).toBeInTheDocument();`.

`ragFolderPicker.test.tsx`:
- top-level folders in `api()`: `[{ name: "code", path: "code" }, { name: "jira", path: "jira" }]`;
- in "Up returns to the parent folder": `findByRole("button", { name: "jira" })`;
- in "a typed path is trimmed of slashes": type `" /jira/notes/ "` and expect
  `{ source_path: "jira/notes" }`;
- add:

```tsx
test("starts at the DATA_ROOT top level and only connects existing folders", async () => {
  const fetchMock = api();
  picker();
  expect(await screen.findByRole("button", { name: "jira" })).toBeInTheDocument();
  expect(screen.getByText("DATA_ROOT")).toBeInTheDocument();
  expect(fetchMock.mock.calls[0][0]).toBe("/api/rag/folders");
  expect(screen.getByText(/The folder must already exist/)).toBeInTheDocument();
  expect(screen.queryByText(/rag-data|is created/)).toBeNull();
});
```

`ragCollections.test.tsx` and `ragFileLog.test.tsx` use `rag-data` only as fixture
data; run
`perl -pi -e 's#/work/rag-data/#/work/data/#g; s#rag-data/##g' services/admin-ui/app/components/__tests__/ragCollections.test.tsx services/admin-ui/app/components/__tests__/ragFileLog.test.tsx`.

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-ui && pnpm exec tsc --noEmit; pnpm test`
Expected: FAIL. `tsc` reports `data_root` missing on `RagConfig` and `variable`
missing on `StorageLocation`; the storage and picker tests fail on the new texts.

- [ ] **Step 3: Implement**

`lib/storage.ts`:

```ts
export type StorageArea = 'data' | 'code' | 'code_index' | 'jira' | 'documents' | 'bootstrap';
export interface StorageLocation {
  id: StorageArea; container_path: string | null; host_path: string | null;
  variable: string; status: 'available' | 'unavailable' | 'unconfigured';
}
```

`lib/rag.ts`: replace `RagArea` and `RagConfig` with

```ts
export interface RagDataRoot { container_root: string | null; host_root: string | null; jira_folder: string | null }
export interface RagConfig { revision: number; settings: RagSettings; embedding_model_id: string | null; data_root: RagDataRoot }
```

`StorageOverviewView.tsx`:
- import `HardDrive` and `Ticket` from `lucide-react` alongside the existing icons;
- `areas` becomes (the `code`, `code_index`, `documents` and `bootstrap` entries are
  today's, unchanged):

```tsx
const areas = {
  data: { name: 'Data folder', icon: HardDrive, purpose: 'DATA_ROOT, the one host folder that holds every folder below. RAG can index any folder in it; each service writes only to its own folder.', layout: 'Top-level folders: code, code graphs, document exports, Jira exports and bootstrap docs, plus folders added for RAG.', link: '/rag/collections', action: 'Manage RAG collections' },
  code: { name: 'Source code', icon: FolderGit2, purpose: 'Mapped project folders and Git clones. Agents read the folders selected in their configuration.', layout: 'Project → one or more folders. Git pulls update the configured clone folder.', link: '/code-projects', action: 'Manage code projects' },
  code_index: { name: 'Code graphs', icon: Database, purpose: 'CodeGraph indexes and source hashes created explicitly in Phase 1. Documentation uses the inspected snapshot.', layout: 'Project ID → source mirror and content hash → graph.sqlite and summary.json. Index data stays separate from source checkouts.', link: '/documentation/generations', action: 'Generate and inspect graphs' },
  jira: { name: 'Jira exports', icon: Ticket, purpose: 'One Markdown file per exported ticket. Add this folder to a collection in RAG → Collections to search tickets.', layout: 'One <KEY>.md file per ticket. Re-exporting a ticket replaces its file.', link: '/jira/tickets', action: 'Export Jira tickets' },
  documents: { name: 'Document exports', icon: FileText, purpose: 'Markdown and D2 files exported from managed documents. PostgreSQL holds the original documents and version history.', layout: 'Document slug → v1, v2, … → README.md and architecture-N.d2. Generation exports automatically; manual documents have an Export action.', link: '/documents', action: 'Manage documents' },
  bootstrap: { name: 'Bootstrap docs', icon: Folder, purpose: 'The original startup directory, seeded with example Markdown. This is not the current RAG source folder or the generated-document destination.', layout: 'Legacy startup files. Add new knowledge through RAG files or managed Documents.', link: null, action: null },
} as const;
```

- in the location cell, `Host · {location.variable}` replaces
  `Host · {location.host_variable}`, and the `host_path_relative` `<dd>` is deleted;
- the alert in "How files become knowledge" becomes:
  `Documents are written to the document export folder and Jira exports to the Jira export folder. Connect these folders to a collection in RAG → Collections and refresh it to make them searchable.`

`RagSettingsView.tsx`: the first `FieldSet` becomes

```tsx
      <FieldSet className="rounded-lg border p-5"><FieldLegend>Data folder</FieldLegend>
        <FieldDescription>Collections connect folders inside DATA_ROOT. The host folder and the Jira folder are set in the deployment's .env.</FieldDescription>
        <Table><TableHeader><TableRow><TableHead>Host folder</TableHead><TableHead>In container</TableHead><TableHead>Jira exports</TableHead></TableRow></TableHeader><TableBody>
          <TableRow><TableCell>{config.data_root.host_root ? <code className="break-all">{config.data_root.host_root}</code> : "Not provided"}</TableCell>
            <TableCell>{config.data_root.container_root ? <code className="break-all">{config.data_root.container_root}</code> : "Not mounted"}</TableCell>
            <TableCell>{config.data_root.jira_folder ?? "Not configured"}</TableCell></TableRow>
        </TableBody></Table></FieldSet>
```

`RagFolderPicker.tsx`:
- component comment: `/** Browse DATA_ROOT or type a folder path, then connect the existing folder to the collection. */`;
- `SheetDescription`: `Choose a folder under DATA_ROOT or type its path, such as code/payments or jira. The folder must already exist.`;
- the path `FieldDescription`: `Relative to DATA_ROOT, such as jira or code/payments.`

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd services/admin-ui && pnpm exec tsc --noEmit && pnpm test`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-ui
git commit -m "feat(admin-ui): storage overview, RAG data folder and folder picker at the DATA_ROOT top level

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: admin-ui — the Jira page names the configured folder; code browser label

**Files:**
- Modify: `services/admin-ui/app/lib/api.ts` (`JiraTickets`)
- Modify: `services/admin-ui/app/components/JiraTicketsView.tsx`
- Modify: `services/admin-ui/app/components/CodeProjectsView.tsx`
- Modify: `services/admin-ui/app/components/__tests__/jira.test.tsx`

**Interfaces:**
- Consumes: `export_folder` from `GET /api/jira/tickets` (Task 7).
- Produces: `JiraTickets.export_folder: string`.

- [ ] **Step 1: Write the failing tests**

In `jira.test.tsx`:
- `snapshot` gains `export_folder: "exports/jira"`:
  `const snapshot: JiraTickets = { connection: …, tickets: […], export_folder: "exports/jira" };`
- in "export sends only the selected ticket and links to RAG collections":
  `expect(screen.getByText("0 files saved, 1 unchanged in exports/jira. 5 comments included.")).toBeInTheDocument();`
  and add
  `expect(screen.getByText(/Add exports\/jira to a collection in/)).toBeInTheDocument();`
- in "the export has no collection field":
  `expect(screen.getByText(/Each selected ticket becomes exports\/jira\/<KEY>\.md/)).toBeInTheDocument();`
  `expect(screen.getByText(/Collections that hold exports\/jira index the tickets/)).toBeInTheDocument();`
  `expect(screen.queryByText(/rag-data/)).toBeNull();`

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-ui && pnpm exec tsc --noEmit; pnpm test -- jira`
Expected: FAIL. `tsc` reports `export_folder` is not in `JiraTickets`, and the page
still says `rag-data/jira`.

- [ ] **Step 3: Implement**

`lib/api.ts`: add `export_folder: string;` to `interface JiraTickets`.

`JiraTicketsView.tsx`: after the `data` state, add
`const folder = data?.export_folder ?? "";` and replace the three texts:
- `FieldDescription`: `Each selected ticket becomes {folder}/&lt;KEY&gt;.md, a Markdown file with its description and all comments visible to the Jira user. Re-exporting refreshes that ticket’s file. Collections that hold {folder} index the tickets at their next sync.`
- summary: `… unchanged in {folder}. {…} comments included.`
- link line: `Add {folder} to a collection in <Link …>RAG → Collections</Link> if it is not in one yet, then refresh that collection to update search.`

`CodeProjectsView.tsx`: `{directory.host_path ?? 'CODE_FOLDER'}` replaces
`{directory.host_path ?? 'CODE_PATH'}`.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd services/admin-ui && pnpm exec tsc --noEmit && pnpm test`
Expected: PASS. `git grep -n "rag-data\|RG_DATA\|CODE_PATH" -- services/admin-ui/app` prints nothing.

- [ ] **Step 5: Commit**

```bash
git add services/admin-ui
git commit -m "feat(admin-ui): the Jira page names the configured export folder

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Docs, full verification and the confirmed `.local` archive move

**Files:**
- Modify: `deploy/README.md`, `README.md`, `docs/rag.md`, `docs/jira.md`,
  `docs/user-guide.md`, `docs/architecture.md`, `docs/code-processing.md`,
  `docs/documentation.md`, `docs/database.md`, `services/rag/README.md`,
  `services/admin-api/README.md`, `examples/docs/index.md`

**Interfaces:**
- Consumes: the behaviour of Tasks 1–9. The docs repeat the Global Constraints'
  variable names, defaults and error texts exactly.

- [ ] **Step 1: Write the docs**

`deploy/README.md`:
- the `repository-worker` bullet: it owns the writable `CODE_FOLDER` and
  `CODE_INDEX_FOLDER` binds; everything else sees `DATA_ROOT` read-only at `/work/data`;
- `## Data folders`: keep the `make setup DATA_ROOT=…` block; replace the table with
  `| Variable | Default | Container path | Contents | Writable in |` rows for
  `DATA_ROOT` (`/work/data`, everything, read-only base), `CODE_FOLDER`,
  `CODE_INDEX_FOLDER`, `DOCS_FOLDER`, `GENERATED_DOCS_FOLDER`, `JIRA_FOLDER` (with the
  writer services from the Global Constraints); the rules (relative, no leading `/`,
  `..`, hidden or empty segments, no overlap; `make setup` stops naming the variable);
  that RAG sees all of `DATA_ROOT`, so it holds only the configured folders (and
  folders added deliberately for RAG) and `make setup` warns about anything else;
  that backups and logs belong in a sibling folder such as `DATA_ARCHIVE`;
- replace the overrides paragraph with **Upgrading from per-folder overrides**:
  `CODE_PATH`, `CODE_INDEX_PATH`, `RG_DATA`, `GENERATED_DOCS_PATH`, `DOCS_PATH` and
  `RAG_SOURCE_ROOT` are refused by `make setup` with the replacement variable; an
  override inside `DATA_ROOT` translates to the relative value; one outside must be
  moved in; `make setup` moves `DATA_ROOT/rag-data/jira` to `JIRA_FOLDER` once and
  migration 0067 rewrites the RAG paths (default `JIRA_FOLDER` assumed; with another
  value the collection shows Folder missing until its folder is replaced); other
  `rag-data/…` folders are not configured folders: move them out, and a collection
  that used them shows Folder missing;
- **Moving the data**: drop "remove any overrides".

`docs/rag.md`:
- intro: the Jira export writes `jira/<KEY>.md` (`JIRA_FOLDER`);
- `## Collections` folder row: a folder is any path relative to `DATA_ROOT` whose
  segments use letters, digits, `.`, `_` and `-` and do not start with `.`; examples
  `jira` → `jira`, `payments` → `code/payments`, `jira` and `docs/runbooks`; delete the
  sentence that a missing `rag-data` folder is created; Add folder refuses a missing
  folder with `Folder does not exist`;
- replace `## Data areas and mounts` with `## DATA_ROOT and mounts`: one read-only
  mount at `/work/data`; the only writable folder for RAG is `JIRA_FOLDER`
  (`JIRA_EXPORT_ROOT`); `DATA_ROOT_DIR` and `DATA_ROOT` variables; host paths are
  `DATA_ROOT/<folder>`; the folder browser starts at the top level of `DATA_ROOT` and
  hides hidden folders and links; the descriptor-based no-follow walk now starts at
  `/work/data`;
- new `### Upgrading to 0067`: Jira paths move, check relaxed, saved agent versions
  rewritten, rollback behaviour (`NOT VALID`);
- API tables: `POST /collections/{id}/paths` refuses a missing folder (409);
  `GET folders` without `path` lists the top level; `GET file?path=<JIRA_FOLDER>/…`
  and `PUT file` work only inside `JIRA_FOLDER` (403 elsewhere); `GET settings`
  returns `data_root`;
- every example path `rag-data/jira/DEMO-1.md` becomes `jira/DEMO-1.md`.

`docs/jira.md`: every `rag-data/jira` becomes "the Jira folder (`JIRA_FOLDER`, default
`jira`)"; the API table notes `export_folder` in the tickets response.

`docs/user-guide.md`: §3 (`jira` for Jira), the setup paragraph (creates `code/`,
`code-index/`, `docs/`, `generated-documents/`, `jira/`, warns about anything else),
§5.3/§5.8/§6 Jira wording, the code folder sentence (`DATA_ROOT/<CODE_FOLDER>`,
mounted read-only under `/work/data`), the Storage paragraph (Data folder plus the five
folders with their `.env` variables) and the `.env` table (the five `*_FOLDER` rows
replace the override rows).

`README.md`: line 176 (`DATA_ROOT/code`, `CODE_FOLDER`), lines 204–208 (collections
hold any folder in `DATA_ROOT`; RAG mounts it read-only and writes only `JIRA_FOLDER`).

`docs/architecture.md` (lines 3 and 218), `docs/code-processing.md` (line 21),
`docs/documentation.md` (line 195), `docs/database.md` (line 83),
`services/rag/README.md` (lines 6–7, 26–29), `services/admin-api/README.md`
(lines 92–94) and `examples/docs/index.md` (lines 23–24): replace the per-area mounts,
`RG_DATA`, `RAG_SOURCE_ROOT`, `CODE_PATH` and `GENERATED_DOCS_PATH` with `DATA_ROOT`
at `/work/data` and the `*_FOLDER` variables. `docs/runtime-verification.md` is a
historical log and stays.

- [ ] **Step 2: Run every suite**

Run:
```bash
cd services/rag && uv run --frozen pytest -q
cd ../admin-api && uv run --frozen pytest -q
cd ../docs-wiki && uv run --frozen pytest -q
cd ../admin-ui && pnpm exec tsc --noEmit && pnpm test
cd ../.. && python3 -m unittest discover -s tests && bash db/tests/run.sh && make compose-validate
```
Expected: everything passes. For any failure, check out `main` in a separate worktree
and run the same test. Accept it only if it also fails there, and record it in the PR
description.

- [ ] **Step 3: Check for leftovers**

Run: `git grep -n "RG_DATA\|RAG_SOURCE_ROOT\|CODE_PATH\|CODE_INDEX_PATH\|GENERATED_DOCS_PATH\|DOCS_PATH\b\|area_root\|AREA_ROOTS" -- . ':!docs/superpowers' ':!docs/runtime-verification.md' ':!db/migrations'`
Expected: only `scripts/setup.py` (`REMOVED`), `tests/test_setup.py`,
`services/docs-wiki/tests/test_deploy.py` (the `removed` set) and the upgrade sections
of `deploy/README.md` and `docs/rag.md`.

Run: `git grep -n "rag-data" -- services/rag/src services/admin-api/src services/admin-ui/app deploy .env.example`
Expected: only `services/admin-api/src/knowledge_admin/collections.py`
(`LEGACY_RAG_ROOT`).

- [ ] **Step 4: Commit**

```bash
git add deploy/README.md README.md docs/rag.md docs/jira.md docs/user-guide.md docs/architecture.md \
  docs/code-processing.md docs/documentation.md docs/database.md services/rag/README.md \
  services/admin-api/README.md examples/docs/index.md
git commit -m "docs: one data folder with named subfolders

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 5: Manual, confirmed — archive this machine's extra `DATA_ROOT` entries (not code)**

This machine's `DATA_ROOT` is `/Users/graovic/fincore/knowledge-agent-v3/.local`.
Besides the configured folders it holds `backups/`, `users/`, `benchmarks/`,
`rag-data/demo` (test data of the `demo` collection) and about 30 `*.log` files.
1. Run `ls -A .local` and show the user the exact list of entries to move to the
   sibling `.local-archive/` (the `DATA_ARCHIVE` for this setup): `backups`, `users`,
   `benchmarks` (not named in the spec; ask about it explicitly), every `*.log`, and
   `rag-data/demo`. **Stop and wait for the user's confirmation.** Move nothing
   without it; delete nothing in any case.
2. After confirmation: `make down`; then
   `mkdir -p .local-archive/rag-data && mv .local/backups .local/users .local/*.log .local-archive/`,
   `mv .local/benchmarks .local-archive/` only if confirmed, and
   `mv .local/rag-data/demo .local-archive/rag-data/`.
3. `make setup` moves `.local/rag-data/jira` to `.local/jira`, removes the now-empty
   `.local/rag-data`, and prints no warning. Then `make up` (runs migration 0067).
4. Ask the user whether to remove the `demo` collection or reconnect it to another
   folder in RAG → Collections; do what they choose. Refresh the `jira` collection and
   confirm a search returns a Jira ticket.
