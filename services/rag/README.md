# RAG retrieval and processing

The v3 runtime stores processed text, file hashes, and vectors in PostgreSQL. It indexes
**collections**: each holds one or more paths (folders), added by sources such as Jira,
Documents and code projects, inside five data areas mounted at `/work/code`,
`/work/code-index`, `/work/rag-data`, `/work/generated-documents` and `/work/docs`
(read-only except `rag-data`). View collections and their paths, refresh,
processing jobs and search in **Administration → RAG**. Manage embedding
connections in **LLM Settings**, then select one in **RAG → Settings**.

## Run the v3 workspace

Docker starts `rag serve-workspace --host 0.0.0.0`. The separate
`rag-workspace-worker` consumes processing jobs from PostgreSQL. Startup performs
no indexing; an administrator refreshes a collection from Collections (a sync job per collection).
Unchanged files skip processing, and successful jobs publish changes atomically.

For native development:

```sh
uv sync --frozen
uv run rag serve-workspace --env-file /path/to/runtime.env
uv run rag-workspace-worker   # or: uv run rag worker
```

Both processes need PostgreSQL connection variables, `RAG_SOURCE_ROOT` (the local
`rag-data` directory), the other area roots (`CODE_ROOT`, `CODE_INDEX_ROOT`,
`DOCUMENT_OUTPUT_ROOT`, `BOOTSTRAP_DOCS_ROOT`) and `CREDENTIAL_ENCRYPTION_KEY`. The host path
variables (`RG_DATA`, `CODE_PATH`, ...) are display metadata and are not resolved inside the container. The API also
requires `RAG_BACKOFFICE_KEY`. Native workers require their environment to be
exported. Per-collection settings and the global embedding model are saved through the admin API.

The service exposes authenticated `/backoffice/projects/{project_id}/...` and
`POST /search` routes. The admin API binds `/api/rag/...` requests to the single
workspace and supplies the service key. Reader and search-agent integrations use
these same workspace APIs. See the [RAG administration guide](../../docs/rag.md)
for endpoint schemas, collections, freshness, incremental processing and storage.

## Chunk-contract check

`rag index <docs-folder>` reads every Markdown file under a folder and reports
chunk-contract violations. It writes nothing and never fails on a violation; the
stdout summary is `<folder>: checked N`, with a `, chunk-warnings N` suffix when
there are warnings. The `app-docs` plugin's authoring loop feeds the warnings
back for revision (`make index TARGET=<docs-folder>` runs the same check).

| Class | stderr format | Fires when |
|---|---|---|
| Section split | `rag index: warning: <collection>: <path> > '<heading_path>': <chars> chars, split into <n> chunks` | A section's text exceeds the 2000-character chunking cap and is split. |
| Heading-less doc | `rag index: warning: <collection>: <path>: no headings -- content not deep-linkable` | The document has no heading at all (no H1 title either). |
| Duplicate heading text | `rag index: warning: <collection>: <path>: duplicate heading '<text>' (x<count>) -- anchors suffixed` | The same heading text appears more than once, at any level or nesting. |

## Chunking

`rag.structure.parse_structure` parses a document once with `markdown_it` and
yields its headings and H2/H3 sections. `rag.chunking.chunk_sections` splits a
section longer than the cap at blank-line paragraph boundaries, never inside a
fenced code block when a safe boundary exists, carrying the previous chunk's
last paragraph as overlap. Text with no safe boundary falls back to a fixed
character slice with overlap. The workspace worker uses the collection's chunk
size and overlap settings.

## Development

`api.py` builds the HTTP app (`/health` plus the workspace router from
`workspace_api.py`); `workspace.py`, `workspace_search.py`, `workspace_models.py`
and `collections.py` hold the collection settings, search, embedding models and
data-area paths; `workspace_worker.py` runs sync jobs; `chunking.py` and
`structure.py` turn Markdown into chunks. `cli.py` exposes `serve-workspace`,
`worker` and `index`.
