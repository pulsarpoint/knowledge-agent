# Collections with source paths — design

Date: 2026-10-06. Status: draft for review. Builds on
`2026-10-05-rag-collections-design.md`.

## Goal

RAG becomes an indexing service with a small API for external components:

1. **Create a collection:** a named search index with its own search settings.
2. **Add a path to a collection:** "my data is here (a folder in `DATA_ROOT`);
   index it into collection X".
3. **Remove a path** when the source no longer wants it indexed.
4. **Sync** a collection, and **search** collections, as today.

Sources own their files and decide which collection their data goes into. One
collection can hold several paths, possibly from different sources. For example,
`payments` could hold `code/payments`, `rag-data/jira` and a runbooks folder.
Admin → RAG → Collections becomes a view of collections and their paths, with
search settings and Refresh; it no longer creates collections or uploads and edits
files.

## Model

- **Collection:**
  - `name` (`^[a-z0-9]+(-[a-z0-9]+)*$`, at most 60 characters, unique among
    non-deleted collections);
  - `description`;
  - search settings: search mode, embeddings, include and exclude patterns,
    chunking;
  - revision, `synced_revision`, `last_synced_at`, `deleted_at`.

  It no longer has a `source_path`.
- **Collection path** (new table `ka.rag_collection_paths`):
  - `collection_id`;
  - `source_path`, relative to `DATA_ROOT` with the same validation as today
    (first segment one of `code`, `code-index`, `rag-data`, `generated-documents`,
    `docs`);
  - an optional owner: `owner_kind` (`documents` | `jira` | `code_project` | NULL),
    `owner_id` (uuid, nullable) and `owner_key` (text, nullable, for example a code
    project's folder);
  - `created_at`.
- **Path rules:**
  - within one collection, paths may not overlap (one may not contain another),
    so a file never appears twice;
  - different collections may share paths.
- **Indexed files** (`rag_files`):
  - `path` becomes the full path under `DATA_ROOT` (for example
    `code/payments/src/retry.py`), unique per collection;
  - a new `path_id` links each file to its collection path;
  - removing a path removes its files from the index (chunks cascade).

### Migration 0065

- Each existing collection's `source_path` becomes its single collection path.
- Owners are backfilled:
  - `documents` (`generated-documents`) gets owner `documents`;
  - `jira` (`rag-data/jira`) gets owner `jira`;
  - everything else stays unowned.
- Indexed file paths are rewritten to `source_path || '/' || path`, and
  `rag_collections.source_path` is dropped.
- **The down migration** restores `source_path`. It refuses when any collection has
  more than one path, with a message to remove the extra paths first.

## RAG API (backoffice, `x-rag-key`)

| Call | Meaning |
| --- | --- |
| `PUT collections/by-name/{name}` with `{description?}` | Create the collection if missing (default settings) and return it. Idempotent; never changes the settings of an existing collection |
| `PUT collections/by-name/{name}/paths` with `{source_path, owner_kind?, owner_id?, owner_key?}` | Add a path to the collection, creating the collection if missing. Idempotent. If the same owner (kind, id, key) already has a path in this collection, that path is replaced (re-pointed). 409 when it overlaps another path of the collection |
| `DELETE collections/by-name/{name}/paths?source_path=` or `?owner_kind=&owner_id=&owner_key=` | Remove a path and its indexed files. Idempotent |
| `DELETE paths?owner_kind=&owner_id=&owner_key=` | Remove an owner's path from every collection (used when a source is switched off or archived) |
| `PUT collections/{id}` | Search settings only (description, mode, embeddings, include/exclude, chunking, revision). Name is not editable |
| `DELETE collections/{id}` | Allowed only when the collection has no source-owned paths. Otherwise 409 naming the sources |
| `POST collections/{id}/sync`, `POST collections/sync-stale` | As today, over all paths of the collection |
| `GET collections` | Each row now has a `paths` list (`source_path`, owner, host path, present, files) |
| `GET collections/{id}/files` | As today, with full paths |
| `GET file?collection=&path=` | `path` is the full path under `DATA_ROOT`. It must lie under one of the collection's paths and be indexable (same rules as today) |
| `PUT file` with `{path, content, expected_hash}` | Internal writer for `rag-data` only, used by Jira export. `path` is the full path under `DATA_ROOT` |
| `POST collections` (create with folder), `DELETE file` | Removed |

## Sync, freshness and search

- **Sync** walks every path of the collection with the collection's include and
  exclude patterns and indexes the files under their full paths.
  - The manifest check, revision isolation, vector reuse and limits work as today.
    The 20,000-file and 200 MB limits apply per collection.
  - If any path is missing, the sync fails with "Path <p> is missing". Nothing is
    removed, so an unmounted folder never empties the index.
- **Freshness:**
  - `folder_changed_at` is the newest change across all paths;
  - the status is `folder_missing` when any path is missing, and each path shows its
    own `present` flag;
  - adding or removing a path bumps the collection revision, so it shows "Needs
    refresh".
- **Search:** hits carry `collection` and the full path, for example
  `code/payments/src/retry.py`.
- **Reader and agents:**
  - the Reader document path stays `<collection>/<path>`, where `<path>` is now the
    full path;
  - agent `read_document(collection, path)` uses the full path;
  - non-grounded document agents get every path of their granted collections as
    read roots.

## Sources

| Source | Path it adds | Collection |
| --- | --- | --- |
| Docs → Documents | `generated-documents` (owner `documents`) | `documents` (existing) |
| Jira | `rag-data/jira` (owner `jira`) | Chosen in the Jira export dialog, default `jira`; created if missing |
| Code project | each mapped folder `code/<path>` (owner `code_project`, id = project, key = folder) | Chosen on the code project page when **Searchable** is on, default the project's slug; created if missing |

- **Jira:** export ensures the path is in the chosen collection before writing
  `rag-data/jira/<KEY>.md`. Choosing a different collection on a later export moves
  the Jira path, because the same owner is re-pointed.
- **Code projects:**
  - **Searchable** on adds all mapped folders to the chosen collection;
  - off, or archiving the project, removes the project's paths everywhere;
  - editing the folders adds and removes paths to match;
  - changing the collection moves the paths.
- Hand-written content goes through Docs → Documents.

## admin-api

- **`rag_collections.py` client:** `ensure_collection(name)`,
  `add_path(collection, source_path, owner)`, `remove_paths(owner)` and
  `remove_path(collection, owner)`, built on the existing RAG request helper.
- **Jira export:** keeps a `collection` field (default `jira`, slug pattern) and
  calls `add_path` before writing. A RAG error fails the export with its message.
- **Code projects:**
  - **`PUT /api/code-projects/{id}/search`** with
    `{enabled, collection?}` adds or removes the project's paths and returns
    `{searchable, collection}`;
  - **`GET /api/code-projects`** includes `searchable` and `collection`, read from
    RAG, or stored on the repository row if simpler (the plan decides);
  - **folder edits and archiving** keep RAG in sync after the project is committed;
  - **a failure** shows on the page with **Retry** and never rolls back the project.
- **Agent config:** a `documents.collections` item becomes
  `{id, name, description, paths: [container paths]}`. The legacy shim maps an old
  item to `paths: [path]`.

## Admin UI

- **RAG → Collections:**
  - "New collection" is removed;
  - each row shows the collection's paths with their sources (Documents, Jira, Code
    project <name> linked to its page, or Unowned), files, embedded chunks, last
    synced, status and Refresh;
  - **Edit** changes search settings only;
  - **Remove** appears only when no source owns a path;
  - an unowned path can be detached from the detail page.
- **Collection detail:** read-only files with full paths and their chunks; no upload
  or edit.
- **Code projects:** a **Searchable** switch plus a collection name field
  (default the slug; an existing collection can be picked).
- **Jira export dialog:** the target collection is a free name or an existing one
  (default `jira`); the notice links to the collection.

## Out of scope

- Automatic sync after a source writes; Refresh stays manual.
- Per-path include patterns (patterns stay per collection).
- Deleting files when a path is removed.

## Errors

| Problem | Behaviour |
| --- | --- |
| A path overlaps another path in the same collection | 409 "Path overlaps <other> in collection <name>" |
| A path is missing on disk | Collection status Folder missing, naming the path; sync refused; index kept |
| Removing a collection with source-owned paths | 409 naming the sources |
| RAG unavailable during a source action | The source shows the error; Jira export fails; code project shows Retry |

## Testing

- **Migration:** paths created from `source_path`, owners backfilled, file paths
  rewritten; the down migration refuses multi-path collections and otherwise
  restores.
- **RAG:**
  - ensure collection, add, re-point and remove a path (idempotency, overlap 409);
  - sync over two paths, and a missing path keeping the index;
  - freshness across paths;
  - full paths in hits and files;
  - `GET file` under a path only;
  - settings-only edit; remove guarded by owners;
  - removed endpoints return 404 or 405.
- **admin-api:**
  - Jira export adds its path to the chosen collection and re-points on change;
  - code project Searchable on, off, folder edit and archive;
  - agent config `paths` and the legacy shim;
  - read roots.
- **docs-wiki:** document paths with full paths.
- **UI:** Collections without New or Upload, with paths and sources, settings-only
  edit, owner-guarded remove; code project Searchable; Jira collection field.
