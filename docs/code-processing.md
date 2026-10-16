# Code processing

Code graphs are generated from **Code → Code graphs** and read by agents (code graph tools come with the tool catalog). Building a graph does not run an LLM.

## Generate and inspect the graph

Generate a graph from **Code → Code graphs** (or per project under Code projects) with **Generate graph**. A durable `code_index` job runs in the repository worker. Inspect the file graph and click a file to view its symbols and relationships. Graph displays are paginated (200 nodes, up to 1,500 edges), with filtering, pan and zoom; edges are limited to the visible page.

The worker uses the public library API of [CodeGraph](https://github.com/colbymchenry/codegraph), pinned to npm version 1.6.0 with package integrity checks. The library supports multiple languages, including Go, Python, TypeScript, Java, Rust and C#. No CLI agent installer, daemon, watcher or application code runs. Telemetry is disabled.

The mounted source is read-only for analysis. A filtered mirror under `CODE_INDEX_ROOT` allows CodeGraph to maintain its local SQLite index without writing `.codegraph` into Git checkouts. Symlinks, credentials files, `.env*`, dependency/build directories, binaries and files over 1 MiB are excluded. Source hashes cover the filtered files, selected folders, missing folders and analyzer/pipeline versions. The application hashes file content on each request; unchanged snapshots are reused, and added, changed and deleted files update the index incrementally.

The host folder `DATA_ROOT/<CODE_INDEX_FOLDER>` (default `code-index`) is mounted under `/work/data`. Storage overview includes this location. Each project directory contains the current source mirror, a manifest with file hashes, and immutable content-hash directories containing `graph.sqlite` and `summary.json`. Graph snapshots are retained so previously queued jobs retain their evidence. There is currently no automatic pruning of old snapshots.

## Limits of the graph

CodeGraph extracts structural evidence, not a verified execution trace. Unsupported files, parsing errors and unresolved calls must stay visible. SQL schemas and queries remain available to source inspection, but CodeGraph does not itself produce a complete PostgreSQL state-transition model. The documentation prompt requires that analysis and must report gaps rather than claim full coverage. A separate deterministic SQL/state extractor remains future work.

## Development verification

Install `services/code-graph` dependencies with `npm ci --prefix services/code-graph --ignore-scripts`. Docker uses Node 22 alongside Python; the public CodeGraph API requires Node 22.5 or newer. The real integration test indexes Go and Python, checks call edges, source isolation, incremental updates/deletions, unchanged-file reuse, immutable snapshots and freshness validation. No provider credentials or remote SSH server are required for these tests.

### Go module roots and native graph queries

For a selected folder containing `go.mod`, CodeGraph now indexes that folder as
its project root. The old parent-root layout parsed symbols but lost Go package
import resolution. Graph node paths are relative to the returned `code_root`;
consumers must use that value, not assume every path starts at `CODE_ROOT`.
The source mirror remains outside the code mount, and code is never compiled or
executed. This works when the common selected folder is a Go module root;
separate modules in disjoint selected folders still need separate module graphs
for reliable import resolution. The pipeline version change invalidates old
processing fingerprints: generate the graph again after upgrading.
Existing graph snapshots and documents are retained.

The agent's `code.graphs` comes from the generated graph (`current_graph` in `capabilities/code_graph.py`), read live
from the project's manifest and snapshot when a run starts, never from a saved version. It lists the graph's folders
and `generated_at`; it is empty when the project was never generated, its snapshot is gone or its folder selection
changed. If the project was synced after the graph was generated, a note says so. `code_graph` is offered in every run and
answers only for a code project of the run that has such a graph.

Agent graph queries use CodeGraph's public SDK through `query.cjs`. The SDK opens
a temporary copy because v1.6.0 does not honor its `readOnly` option. Source is
copied into that disposable project only for requested nodes, after verifying
selected-folder boundaries and the indexed content hash. No reindexing, custom
SQL, automatic sync, or model calls happen during these queries. Storage backup
and the existing administrative graph browser still use SQLite; this change
replaces the agent's graph querying and traversal.
