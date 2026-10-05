# RAG retrieval and processing

The v3 runtime stores processed text, file hashes, and vectors in PostgreSQL. It indexes
**collections**: each is a folder inside one of five data areas mounted at `/work/code`,
`/work/code-index`, `/work/rag-data`, `/work/generated-documents` and `/work/docs`
(read-only except `rag-data`). Manage collections, refresh, uploads (under `rag-data`),
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
uv run rag-workspace-worker
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

## Standalone collection CLI

(Unrelated to the RAG collections above: these are SQLite-backed local collections.)

The independent `index` / `serve` commands below remain available for local
Markdown collections. Their SQLite indexes and MCP tools are separate from the
PostgreSQL workspace used by the v3 application.

### Install and run

```text
cd services/rag
uv sync
uv run rag index /work/my-app-docs      # build/rebuild the index
uv run rag serve /work/my-app-docs      # serve the API (port 8790)
```

Indexing is ALWAYS an explicit step: `rag serve` performs no indexing
at startup unless you pass `--reindex` (one index pass, then serve).
Serving a collection that has never been indexed is not an error —
search returns `[]` and `GET /collections` reports `indexed: false`.

```text
uv run rag index <config.toml | docs-folder> [--env-file PATH]
uv run rag serve <config.toml | docs-folder> [--host 127.0.0.1]
                 [--port 8790] [--env-file PATH] [--reindex]
```

A bare docs-folder argument auto-wraps as a single collection named
after the folder. For multiple collections, pass a `config.toml`:

```toml
[[collections]]
name = "my-app"
path = "/work/my-app-docs"

[llm]                       # embeddings only — chat lives elsewhere
embed_base_url = "http://localhost:11434/v1"
embed_model = "nomic-embed-text"
```

Each `[[collections]]` entry may also set `kind = "docs"` (the default),
`"code"`, or `"memory"` — rag treats every kind identically at index and
search time (a collection is just a folder of markdown, opaque to this
service); the kind only decides which `search_<kind>` MCP tool a
collection feeds (see the MCP facade below).

Environment overrides (see [.env.example](.env.example)):
`RAG_EMBED_BASE_URL`, `RAG_EMBED_MODEL`, `RAG_API_KEY` — API keys
belong only in the gitignored `.env`, never in `config.toml`. With no
embedding config resolved, indexing skips vectors and search runs
BM25-only; `serve` prints a BM25-only notice at startup and
`GET /collections` reports the configured mode in its `retrieval` field,
so a degraded deployment is visible from the console or one API call.
Note `retrieval: "hybrid"` means an embed client is configured, not that
the endpoint is currently reachable — a per-request failure still falls
back to BM25 and logs a warning instead of changing the field.
Note that env vars only reach the process if exported or passed with
`--env-file .env` — a `.env` file next to the config is NOT read
automatically.

For a compose deployment alongside docs-wiki and nginx — an internal
`rag` container docs-wiki reaches over `DOCS_WIKI_RAG_URL`, not exposed
publicly — see the [deploy bundle](../../deploy/README.md).

## API

| Route | Returns |
|---|---|
| `GET /health` | `{"status": "ok"}` |
| `GET /collections` | `[{name, indexed, documents, retrieval: "hybrid"\|"bm25-only"}]` |
| `GET /collections/{name}/documents` | catalog: `[{path, title, quadrant, front_matter}]` |
| `GET /collections/{name}/documents/{path}` | `{path, title, quadrant, front_matter, markdown, headings}` |
| `POST /collections/{name}/search` `{query, top?=8}` | `[{path, title, heading_path, anchor, text, score}]` |

Errors are always `{"error": {"message": ...}}` with a 4xx status
(including body-validation failures, unmatched paths, and wrong
methods); unknown collections 404.

Search semantics: hybrid BM25 + vector retrieval fused with reciprocal
rank fusion. When embeddings are unavailable (no config, endpoint down
mid-request, or vectors still pending), search degrades to BM25-only
rather than failing. Fallback excerpts are FTS snippets while hybrid
excerpts are full chunks; the `score` field is a rank-derived proxy
(`1/(rank+1)`), comparable within a single response only.

## MCP facade

Alongside the HTTP API, `rag serve` mounts an MCP server at `/mcp` on
the **same port**, transport streamable HTTP (the `fastmcp` 3.x
default `http_app()` transport). Point any MCP client at
`http://<host>:<port>/mcp` — no separate process, no separate port.

The tool surface is kind-dynamic: one `search_<kind>` tool is
registered per kind PRESENT in the config (`docs`, `code`, `memory` —
see [`VALID_KINDS`](src/rag/config.py)), fanned out across every
collection of that kind. `read_doc` and `list_docs` are always
registered regardless of configured kinds.

| Tool | Args | Returns | Description |
|---|---|---|---|
| `search_docs` | `query: str, top: int = 8` | `[{path, title, heading_path, anchor, text, score, collection}]` | Search the application documentation (how-to, reference, explanation). Returns excerpts with headings, anchors and relevance scores. |
| `search_code` | `query: str, top: int = 8` | same shape | Search the extracted code catalog: error codes and constants, endpoints, config keys, log strings. Returns cited excerpts with file anchors. |
| `search_memory` | `query: str, top: int = 8` | same shape | Search previously confirmed support solutions (symptom, cause, resolution, evidence). |
| `read_doc` | `collection: str, path: str` | `{path, title, quadrant, front_matter, markdown, headings}` | Fetch one full document by collection and path, with markdown and heading anchors. |
| `list_docs` | `kind: str` | `[{path, title, quadrant, front_matter, collection}]` | List all documents of a kind (docs, code, memory) across its collections. |

Each `search_<kind>` tool runs the *exact same* pipeline as
`POST /collections/{name}/search` (`rag.api.run_search` — hybrid via
`retrieval.retrieve` when embeddings are configured, degrading to
BM25-only on `EmbedError` or when no embed client is configured) once
per collection of that kind, tags each hit with its `collection` name,
merges by score descending, and returns the top `N`. An unindexed
collection of a present kind contributes nothing (same degradation as
the HTTP route) rather than erroring. `top` is bounds-checked to
`1..50`, same bound as the HTTP route's `top` field; an empty/blank
query returns `[]` without touching the index.

Errors never crash the MCP session: an unknown collection, an unknown
document path, an out-of-range `top`, or a `kind` with no configured
collections all raise an MCP tool error carrying a message (e.g.
`"unknown collection: ghost"`, `"no such document: x.md"`, `"top must
be between 1 and 50 (got 51)"`, `"no collections configured for kind
'memory'; configured kinds: docs"`) — never a protocol-level crash.

The standalone collection and MCP routes have no authentication. They are not
published by the Docker gateway; v3 uses the authenticated workspace APIs above.

**Client config (Track C preview).** A codex-style `mcp_servers` TOML
block pointing at a locally running `rag serve`:

```toml
[mcp_servers.rag]
url = "http://localhost:8790/mcp"
transport = "streamable-http"
```

This is a preview of the Track C wiring, not a shipped codex
integration — the block works today against any `rag serve` instance
since the facade needs no additional configuration beyond what
`rag serve` already resolves.

## Index format

Each collection's index lives at `<path>/.docs-wiki/index.db` — the
directory name is kept for compatibility with indexes built by
pre-split docs-wiki installs; either side can read the other's index.
It is fully regenerable: delete it and run `rag index` again.

**Changelog: single-parse markdown (2026-08).** Chunk section boundaries
(`ingest.split_sections`) and heading anchors (`anchors._rendered_ids`)
now both derive from one shared `markdown_it` token parse
(`rag.structure.parse_structure`) instead of each running its own
hand-rolled line scanner. This is a real CommonMark parser, not a regex
scanner, so it is a strict superset of correctness rather than a
byte-for-byte port of the old scanner's behavior — several real,
observable delta classes exist for a document that happens to hit them:

- **Setext headings** (`Title\n=====` / `Title\n-----`) are now
  recognized as H1/H2; the old scanner only matched ATX (`#`-prefixed)
  headings and silently treated setext syntax as body text. Note: a
  `---` line immediately under a paragraph with **no blank line**
  separating them is a common divider idiom in hand-written docs, and is
  now parsed as a setext H2 — the paragraph's text becomes that
  heading's label instead of staying as ordinary body/chunk text. If that
  paragraph was the section's only content, the section is dropped
  entirely from the index (empty-section rule) — the text then exists
  only as a rendered heading, not as searchable chunk text. Sections
  silently missing from search results after upgrade → reindex; worth a
  quick grep (`---` directly under a non-blank line) across existing
  docs after upgrading.
- A trailing closing hash run on an ATX heading (`## Foo ##`) is now
  stripped from the heading text/label (`"Foo"`, not `"Foo ##"`).
- A tab after the hashes (`##<TAB>Foo`), or 1-3 leading spaces before
  them, now count as a valid ATX heading — the old scanner required an
  exact `"## "` at column 0.
- A heading-lookalike line inside a raw HTML block is correctly no
  longer read as a heading (the old scanner had no concept of HTML
  blocks, only fenced code).
- A heading nested inside a blockquote or list item (`> ## x`, `- ## x`)
  is real for anchor purposes (it gets a rendered id, consuming the same
  slug namespace as any other heading) but is never a chunk-section
  boundary — chunker and anchors agree on this by construction, from the
  same shared parse.

**Run `rag index` again after upgrading** — any of the above deltas can
shift section boundaries and/or anchors for existing indexed content,
independent of whether you know your docs use any of these shapes.

## Chunk contract

`rag index` never refuses a document — a doc that violates the contract
below indexes exactly as any other. Instead, `index_collection` reports
each violation to **stderr** as it (re)indexes a changed doc, and the
CLI's stdout summary line gains a `, chunk-warnings N` suffix whenever a
pass produced at least one (the line is byte-identical to before on a
clean pass — the suffix is purely additive). Only docs actually
(re)indexed this pass are checked; an unchanged doc is skipped before its
content is even re-read, so it is not re-warned every run. `code` and
`memory` collections are exempt from the contract entirely (no warnings,
no `chunk_warnings` count, no stdout suffix) — their content is
generated (generated source documentation, agent memory dumps), and the
contract targets authored docs with an author to feed a warning back to.

Three deterministic classes, each a fact the pipeline already computes
while parsing/chunking — no extra parse, no heuristics:

| Class | stderr format | Fires when |
|---|---|---|
| Section split | `rag index: warning: <collection>: <path> > '<heading_path>': <chars> chars, split into <n> chunks` | A section's text exceeded the 2000-char chunking cap and was split into `n` chunks. |
| Heading-less doc | `rag index: warning: <collection>: <path>: no headings -- content not deep-linkable` | The document has literally zero headings (no H1 title either) — everything indexes under the synthetic `"(intro)"` heading path, so nothing in it can be anchor-linked. A doc with just a title and no H2/H3 subsections does NOT trigger this (it has a heading, the title) even though its body still falls under `"(intro)"`. |
| Duplicate heading text | `rag index: warning: <collection>: <path>: duplicate heading '<text>' (x<count>) -- anchors suffixed` | The same heading text (exact match) appears more than once anywhere in the doc — any level, root or nested inside a blockquote/list — matching the full population `render_html`'s slug-dedupe walk consumes. The repeats still index and render fine (`render_html` suffixes their anchor ids, `#foo`, `#foo-2`, ...); the warning just flags that linking by heading text alone only ever reaches the first occurrence. |

The `app-docs` plugin's authoring/build loop feeds these warnings back to
the authoring LLM for revision — see that plugin's docs; `rag` itself
does no enforcement beyond reporting.

**Chunker refinements (2026-08).** Two changes to how an oversized
section becomes multiple chunks and what gets embedded, both purely
index-time:

- **Paragraph-aware splitting.** An oversized section now splits at
  blank-line paragraph boundaries — the boundary closest under the cap —
  instead of a fixed-offset character slice through arbitrary content.
  A blank line inside an open fenced code block is never treated as a
  boundary, using the same real CommonMark fence-close rule this
  codebase already applies in `structure.py`'s parse: a fence closes only on a
  line whose delimiter run is the SAME character (``` vs ~~~) and AT
  LEAST AS LONG as the run that opened it — so a *widened* fence (e.g. a
  four-backtick opener deliberately used to safely wrap a literal
  three-backtick line inside it) is
  tracked correctly end to end: the narrower nested run is content, not
  a close, and every paragraph boundary after the fence's REAL close is
  still used normally. A chunk split never lands mid-fence *as long as
  some other, safe boundary exists*; a fence that is itself (or is part
  of a single paragraph that is) larger than the cap has no safe
  boundary to use and still hard-slices through it — see the fallback
  note below. The previous chunk's last paragraph is carried into the
  next chunk as overlap (a whole paragraph, not a fixed char count — the
  old 200-char slide's replacement, at paragraph granularity) UNLESS
  carrying it would itself push the next chunk over the cap, in which
  case the next chunk starts fresh with no carry. The OLD
  fixed-offset-with-overlap algorithm is still the fallback for text
  with no usable (safe) blank-line boundary at all, or for any single
  paragraph that, by itself, still exceeds the cap after paragraph
  splitting — unchanged from before, and this remains the ONLY case
  where a chunk boundary can land inside a fence.
- **Heading-prefixed embedding input.** A chunk's embedding INPUT (what
  gets sent to the embed endpoint) is now `"<heading_path>\n\n<chunk
  text>"` for any chunk whose heading path isn't the synthetic
  `"(intro)"` — the heading gives the embedding model the same "what is
  this about" context a reader gets from the page. `"(intro)"` chunks
  embed unprefixed (that label isn't real document content). This
  changes ONLY the vector: the indexed chunk `text` column (FTS-searched
  and returned in search results) and everything else about the chunk
  are untouched.

**A plain `rag index` re-run does NOT apply either change to
already-indexed, unchanged docs.** Both refinements only take effect when
a doc is actually (re)chunked/(re)embedded, and `index_collection` only
does that for a new file or one whose content hash changed since the
last index — an unchanged doc is skipped before its body is even
re-read, so its existing chunk boundaries and vectors are left exactly
as they were, indefinitely, no matter how many times you run `rag
index` afterward. To actually apply these refinements across an
existing corpus, force a full reprocess: delete the collection's index
and reindex from scratch (`<path>/.docs-wiki/index.db` is fully
regenerable — see [Index format](#index-format) above) rather than
relying on a plain re-run. (Model/dim invalidation is a separate,
unrelated mechanism and still triggers a re-embed on its own terms
regardless of the content-hash skip.)

## Development

The module layout mirrors the request lifecycle. `store/` (`Store`) is
the ONLY module that touches SQLite — schema, migrations, doc/chunk
reads and writes, BM25 and vector search, meta — every other module
holds a `Store`, never a raw connection. That rule is scoped to `src/`;
tests may still reach into `store.conn` directly as a low-level escape
hatch (e.g. to simulate a legacy pre-migration db, or to assert on
exactly which columns a query selects) without violating it. `ingest.py`
is the indexing
pipeline (`index_collection`): walk the collection's markdown files,
hash for change detection, parse structure/chunk via `structure.py`,
render and write through `Store`, then sync embeddings when configured.
`content.py` holds the pure text-in/text-out helpers — front matter
parsing, quadrant rules, `render_html`, heading-slug dedup — shared by
`ingest.py` (on the indexing path) and `anchors.py` (so the anchor id
sequence and `render_html`'s own ids can never drift apart).
`structure.py` is the single shared `markdown_it` parse both the
chunker (`ingest.split_sections`) and the anchor computation
(`anchors._rendered_ids`) derive from, so there is exactly one opinion
of where a document's headings are. On top of those, `api.py` (the HTTP
routes plus the shared `run_search`/`_attach` pipeline), `retrieval.py`
(hybrid BM25+vector fusion), `anchors.py` (heading → rendered-anchor
resolution), and `mcp_facade.py` (the MCP tool wrappers, which reuse
`api.py`'s pipeline rather than duplicating it) are the retrieval-time
retrieval surface.

## Boundaries

- The configured embedding endpoint is the only network call this
  service ever makes; with no embedding config it makes none at all.
- Read-only over the documentation folders except `.docs-wiki/` (the
  index it owns).
- No auth; binds 127.0.0.1 by default — front it appropriately if you
  bind wider.

See `docs/superpowers/specs/2026-08-03-rag-service-design.md` at the
repository root for the full design.
