# One data folder with named subfolders — design

Date: 2026-10-07. Status: draft for review. Replaces the per-area mounts from the
DATA_ROOT change and the `rag-data` area.

## Goal

The application keeps all its files in one base folder, `DATA_ROOT`. `.env` names that
folder and, relative to it, the folder each source writes to. RAG sees the whole base
folder, so **Add folder** can pick any subfolder (`/code/payments`, `/docs/runbooks`,
`/jira`, or a folder an external tool writes). The `rag-data` area is removed; Jira
exports go to their own folder.

## Configuration (`.env`)

| Variable | Default | Used for |
| --- | --- | --- |
| `DATA_ROOT` | required, absolute host path | Base folder. RAG sees all of it |
| `CODE_FOLDER` | `code` | Code projects: clones and mapped folders |
| `CODE_INDEX_FOLDER` | `code-index` | Code graphs |
| `DOCS_FOLDER` | `docs` | Documents seeded on a fresh install |
| `GENERATED_DOCS_FOLDER` | `generated-documents` | Docs → Documents output |
| `JIRA_FOLDER` | `jira` | Jira ticket exports (`<KEY>.md`) |

- The five folder variables are relative paths inside `DATA_ROOT`: no leading `/`, no
  `..`, no hidden segments, and they may not overlap each other (one may not contain
  another). `make setup` validates them, creates missing folders and stops with a clear
  message otherwise.
- **Removed:**
  - the absolute overrides (`CODE_PATH`, `CODE_INDEX_PATH`, `RG_DATA`,
    `GENERATED_DOCS_PATH`, `DOCS_PATH`);
  - `RAG_SOURCE_ROOT`.

  `make setup` stops when an old override is still set, with the matching new variable
  to use. An override pointing inside `DATA_ROOT` translates directly; one outside it
  needs the folder moved in.

## Containers

- Compose mounts `DATA_ROOT` once at `/work/data`.
  - **Read-only** for RAG, docs-wiki and agents.
  - **Writable** only where a service writes:

    | Service | Writable folder |
    | --- | --- |
    | repository-worker | `CODE_FOLDER` |
    | code processing | `CODE_INDEX_FOLDER` |
    | admin-api | `GENERATED_DOCS_FOLDER` and `CODE_FOLDER` (for removing projects) |
    | RAG | `JIRA_FOLDER` |

    Each is a nested writable bind mount on top of the read-only base.
- Services keep their existing in-container variables, computed by Compose:
  - `CODE_ROOT=/work/data/${CODE_FOLDER}`
  - `CODE_INDEX_ROOT=/work/data/${CODE_INDEX_FOLDER}`
  - `BOOTSTRAP_DOCS_ROOT=/work/data/${DOCS_FOLDER}`
  - `DOCUMENT_OUTPUT_ROOT=/work/data/${GENERATED_DOCS_FOLDER}`
  - new `JIRA_EXPORT_ROOT=/work/data/${JIRA_FOLDER}`

  So code that reads those variables does not change.
- New `DATA_ROOT_DIR=/work/data` (in-container base) and `DATA_ROOT` (host path, for
  display) for RAG and admin-api.

## RAG

- **Folder paths:** a collection folder (`source_path`) is any path relative to
  `DATA_ROOT`.
  - **Allowed:** visible segments of letters, digits, `.`, `_`, `-`; the first segment is
    no longer limited to five area names.
  - **Refused:** hidden segments, `node_modules`, `.git` and `..`, as today.
- **Reads** go through one root, `/work/data`, with the same descriptor walk and
  `O_NOFOLLOW` (no symbolic links, nothing outside the base).
- **Folder browser:** `GET folders` without `path` lists the top-level folders of
  `DATA_ROOT`, for example `code`, `code-index`, `docs`, `generated-documents`, `jira`,
  and any other folder there. Hidden folders and links are not listed.
- **Writable folder:** only `JIRA_FOLDER`.
  - `PUT file` and `GET file` without a collection work only for paths under
    `JIRA_FOLDER`. Today they are limited to `rag-data`.
  - **Add folder** no longer creates missing folders; a missing folder is 409 "Folder does
    not exist".
- **Host paths** shown in the UI are `DATA_ROOT/<source_path>`.

## Jira

Export writes `${JIRA_FOLDER}/<KEY>.md` (default `jira/<KEY>.md`) through RAG's writer,
as today. The Jira dialog and notices name the configured folder (from a small settings
response), not a hard-coded `rag-data/jira`.

## Upgrade

Migration 0067 and `make setup` move existing Jira data:

1. **`make setup`:** if `DATA_ROOT/rag-data/jira` exists and `DATA_ROOT/${JIRA_FOLDER}`
   does not, it moves it there. It prints what it moved.
2. **Migration 0067:** rewrites RAG paths `rag-data/jira…` to `jira…` in
   `rag_collection_paths.source_path`, `rag_files.path` and `rag_file_log.path`. It
   assumes the default `JIRA_FOLDER`; with another value, the collection shows Folder
   missing until an admin replaces the folder (index kept). The down migration rewrites
   back.
3. **Other `rag-data/…` folders** (for example `rag-data/demo`) are not configured folders:
   `make setup` lists `rag-data` in its warning, and a collection that used them shows
   Folder missing once they are moved out.

### Only the configured folders live in `DATA_ROOT`

`DATA_ROOT` holds only the five configured folders (and folders an admin deliberately
adds for RAG). Backups, logs, user state and anything else belong outside it, for example
in a sibling `DATA_ARCHIVE` folder; RAG never sees them.

- **`make setup`:** after creating the folders, it lists any other top-level entries in
  `DATA_ROOT` as a warning, naming each and suggesting it be moved out (for example to
  `DATA_ARCHIVE`). The warning never stops setup, and setup never moves or deletes them.
- **This machine:** `.local` holds `backups/`, `users/`, `rag-data/demo` and about 30 log
  files. A one-time manual step, done only after you confirm, moves `backups/`, `users/`
  and the logs to a sibling `.local-archive/` (the `DATA_ARCHIVE` for this setup). It never
  deletes anything. `rag-data/demo` is test data for the `demo` collection; it moves to
  `.local-archive/` too, and that collection is removed or reconnected in RAG.

## Admin UI

- **Storage overview:** lists `DATA_ROOT` and the five folders with host paths.
- **Folder picker:** starts at the `DATA_ROOT` top level.
- **Jira dialog:** names the configured Jira folder.
- **`rag-data`:** no text mentions it any more.

## Errors

| Problem | Behaviour |
| --- | --- |
| A folder variable is absolute, uses `..`, is hidden, or overlaps another | `make setup` stops naming the variable |
| An old override variable is set | `make setup` stops with the replacement variable |
| `DATA_ROOT` holds other top-level entries | `make setup` warns, naming each; nothing is moved |
| Add folder with a missing path | 409 "Folder does not exist" |
| A Jira write outside `JIRA_FOLDER` | 403 from RAG |

## Testing

- **setup:**
  - defaults;
  - custom relative folders;
  - invalid and overlapping values;
  - old overrides refused;
  - the `rag-data/jira` move;
  - the warning for other top-level entries;
  - idempotency.
- **compose-validate:** each service gets the expected mounts (read-only base, writable
  folders) and variables.
- **RAG:**
  - any top-level folder can be added;
  - the browser lists the base;
  - hidden folders, `..` and links are refused;
  - writes are limited to `JIRA_FOLDER`;
  - reads go through one root;
  - host paths are correct.
- **Migration 0067:** up and down rewrite only `rag-data/jira` paths.
- **admin-api:**
  - Jira export writes to `JIRA_EXPORT_ROOT`;
  - agent read roots map `source_path` to `/work/data/…`;
  - the storage overview is correct.
- **UI:** the storage overview, the picker root and the Jira folder text.
- **Docs:** `.env.example`, `deploy/README.md`, `docs/rag.md`, `docs/jira.md`, the user
  guide.
