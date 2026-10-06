# Code processing and documentation phases

Documentation generation has four explicit phases. It does not run an LLM when building a graph, and completing a phase never starts the next automatically.

## Saved workflows

Documentation → Generations lists saved workflows. **New documentation** selects a service or environment and a model, then opens `/documentation/generations/<workflow-id>`. Source selections, inline prompts and additional instructions autosave to PostgreSQL. Refresh restores these drafts, phase statuses, attempts and token usage. Selected overview/API/messaging attempts and the graph project are retained in the URL. Pending changes trigger a refresh warning; conflicting edits in another page are rejected instead of overwriting the newer draft.

The phase summary links to inspection and rerun controls. Every rerun creates another job and, when successful, another immutable document version. Existing documents expose **View phases / regenerate** links to their workflows. Migration 0055 groups existing attempts by service (or by environment for architecture), linking their historical graph snapshots without rewriting generation records. Separate new workflows for the same service keep separate attempt histories while contributing versions to the same canonical documents.

`ka.documentation_workflows` stores identity and drafts; `ka.documentation_workflow_jobs` associates graph and documentation jobs. Deleting a service or environment preserves workflow history and document versions, but that workflow can no longer generate new documentation. Existing workers remain responsible for execution; refreshing or closing the browser does not cancel a queued or running phase.

## Phase 1: Generate and inspect the graph

Choose a service and its source folders in Documentation → Generations, then **Generate graph**. The same action is available per project under Code projects. A durable `code_index` job runs in the repository worker. Inspect the file graph and click a file to view its symbols and relationships. Graph displays are paginated (200 nodes, up to 1,500 edges), with filtering, pan and zoom; edges are limited to the visible page.

The worker uses the public library API of [CodeGraph](https://github.com/colbymchenry/codegraph), pinned to npm version 1.6.0 with package integrity checks. The library supports multiple languages, including Go, Python, TypeScript, Java, Rust and C#. No CLI agent installer, daemon, watcher or application code runs. Telemetry is disabled.

The mounted source is read-only for analysis. A filtered mirror under `CODE_INDEX_ROOT` allows CodeGraph to maintain its local SQLite index without writing `.codegraph` into Git checkouts. Symlinks, credentials files, `.env*`, dependency/build directories, binaries and files over 1 MiB are excluded. Source hashes cover the filtered files, selected folders, missing folders and analyzer/pipeline versions. The application hashes file content on each request; unchanged snapshots are reused, and added, changed and deleted files update the index incrementally.

The host folder `DATA_ROOT/<CODE_INDEX_FOLDER>` (default `code-index`) is mounted under `/work/data`. Storage overview includes this location. Each project directory contains the current source mirror, a manifest with file hashes, and immutable content-hash directories containing `graph.sqlite` and `summary.json`. Graph snapshots are retained so previously queued jobs retain their evidence. There is currently no automatic pruning of old snapshots.

## Phase 2: Application overview

The first analysis phase has its own **Generate overview / Regenerate overview** button. Select a model and service, generate the selected projects' graphs, then edit the active **Application overview** prompt inline if needed. This phase runs a single Agents SDK agent with `list_files`, `read_file`, `search_files` and `code_graph`; its input contains service information and code/graph references, without embedding all source content in the prompt. SSH and MCP access are disabled.

The overview explains purpose, components, dependencies and representative flows. A small optional structured component diagram is serialized to D2 and rendered with TALA. Missing source produces an explicit document without a model call. Graph freshness is checked at submission, before analysis and after analysis; a source change during the run prevents publishing its output.

The overview uses ordinary agent tool calling during exploration, followed by a structured writing step using the same model and collected conversation. The source tools are disabled for writing, and the Agents SDK returns a Pydantic-validated document. It does not force a provider response schema during exploration: Bedrock's schema adapter can emit a synthetic JSON response before analysis is complete. Empty or placeholder-sized output fails the phase instead of becoming a saved overview.

Analysis is bounded to 20 model turns. The writing step retains the collected evidence even when that limit is reached and explicitly states coverage gaps. It does not restart source analysis or silently increase the budget.

Each run preserves its exact prompt, overrides, graph hashes and selected resources in the existing generation records. Regeneration creates an immutable document version. The inline inspector can select earlier runs and preview that run's exact document version, prompt and source snapshot; a changed graph selection is flagged. Other documentation is never started automatically.

The phase inspector and generation history show input, output and total tokens, plus completed model calls. Overview usage separates source inspection from writing. Usage snapshots are saved in `ka.job_events` after each model response, so completed calls remain visible if a later call or validation fails. Graph generation and skipped analysis use zero tokens. Older runs without recorded usage are labeled accordingly; missing provider usage is marked incomplete. Cache and reasoning counts are shown as details without adding them again to totals.

Artifacts live under `DOCUMENT_OUTPUT_ROOT/services/<service-name>-<short-id>/overview/` (replaced in place on each new version): `overview.md`, `metadata.json` and any `architecture-N.d2` files. PostgreSQL remains authoritative. Metadata records the document/version IDs, source/graph hashes, code paths, prompt version/hash and generation time. The document page's export action can restore files if the initial export failed. The default prompt is an active, editable migration fixture (0053); upgrades preserve existing customizations.

## Phase 3: API documentation

Choose a service and model, review its selected graph, then click **Generate API documentation**. The inline prompt and additional context apply only to this run; saved prompt versions remain editable under Documentation → Prompts. Migration 0054 seeds the active **API documentation** prompt for existing and new workspaces, preserving administrator changes on repeated seeding.

Phase 3 selects API registrations deterministically from CodeGraph route nodes. Each registration supplies its project, source path, line range and candidate handlers. Test and conditional registrations remain distinct.

Each endpoint agent receives these graph locations and the configured code/graph paths. It uses `shell_run` (Bash, Python, `rg`, `ast-grep`), `code_graph` and focused file reads to inspect relevant registration, middleware, handler, function/type/SQL source. Source bundles are not inserted into the initial model request. The current preprocessor still calculates dependency ranges for inventory fingerprints, but the model chooses which bodies to read. Unresolved and external definitions remain explicit evidence gaps.

The reusable `read_file` tool also supports inclusive `start_line`/`end_line` ranges for other agent workflows that inspect graph symbols interactively.

The endpoint agent returns plain Markdown, without forcing a JSON response schema during tool use. `generate_api_endpoint` checks its final answer before saving: it must identify the assigned method/path, contain completed Contract, Authentication and middleware, Processing flow, State and side effects, Responses and examples, and Evidence and gaps sections, and cite the registration with valid source line bounds. Scratch replies such as `wip`, JSON progress objects, and unfinished outlines fail the endpoint and stop the phase; already completed sections and token usage remain available. These are completion and citation-location checks, not automatic verification of every claim. Pipeline version 5 prevents reuse of sections generated before natural completion and Claude caching/reasoning were enabled.

Each endpoint keeps its tools until the agent returns its final Markdown document. There is no model-call limit or forced tool-free writing step. A one-hour wall-clock timeout still fails a stuck endpoint, preserving completed sections and recorded usage. Model progress renews the worker lease, so long phases can finish without losing job ownership. There is no API-specific output-token cap, source-character/hop budget, cumulative token budget or automatic group splitting. Provider limits still apply. The native model adapter resolves the selected model’s declared output allowance before adding transport prefixes. For Claude models whose metadata advertises support, it adds prompt-cache checkpoints to the system message and latest conversation message and requests `xhigh` reasoning (`high` on models without `xhigh`). The typed SDK reasoning setting preserves signed thinking blocks across tool calls. Cache writes and reads are retained in token usage. This does not add automatic conversation compaction. Contracts are assembled deterministically into the final Markdown document.

The phase inspector shows discovered routes and saved sections, including during failed runs. Checkpoints use `ka.job_events` (`api_inventory` and `api_section`); `GET /api/documentation/generations/<job-id>/api-analysis` returns the selected run's inventory and sections. A failed run reuses matching completed sections based on graph/source hash, pipeline version, model configuration, service context and instructions. Regeneration after success runs all endpoints again. Source freshness is verified again before publication.

Static route discovery does not establish complete runtime coverage: unsupported frameworks, generated and dynamic APIs can be absent. Each section must document unresolved evidence rather than invent behavior. An empty route inventory records this limitation without calling a model.

Migration 0058 activates the graph-directed API prompt for unchanged seeded configurations and updates workflow drafts only when their content exactly matches the old fixture. Administrator prompts and edited inline drafts are preserved. Historical generations remain immutable.

Artifacts are saved beside the overview under `DOCUMENT_OUTPUT_ROOT/services/<service-name>-<short-id>/api/` (replaced in place): `api.md`, `metadata.json` and optional `architecture-N.d2` files. Regeneration creates a new version; the inspector shows the exact selected version. API runs have no SSH or MCP access. Their shell can run local inspection scripts against read-only source, without network access or calls to live endpoints.

## Phase 4: Messaging documentation

Click **Generate messaging documentation** to inspect messaging independently using the selected Phase 1 graph and source. Its inline prompt and context autosave with the workflow; the selected attempt survives refresh through `messaging_run` in the URL. Migration 0056 seeds an active **Messaging documentation** prompt for existing and new workspaces. Customized prompt versions remain editable and are preserved.

This phase discovers consumers, producers, broker subscriptions, request/reply handlers, background queues and in-process events actually implemented in the source. It traces message contracts and routing through validation, business handlers, database changes, outgoing messages and acknowledgements. The prompt covers retries, redelivery, ordering, idempotency, dead-letter handling, outbox/inbox consistency and a messaging-to-state matrix, with source references and explicit gaps. NATS Core/JetStream and other transports are documented only when evidenced. No messaging found is distinct from missing source or incomplete inspection.

The existing Agents SDK/model performs up to 40 turns of read-only graph/source inspection, followed by one structured writing step retaining the collected evidence. There is no broker connection, message publication, SSH access or application execution. Source freshness checks, input/output token accounting, failure handling and immutable version history match the API phase. An optional validated message-flow diagram is converted to D2/TALA. No source produces an explicit missing-code document with zero model calls.

Artifacts are saved under `DOCUMENT_OUTPUT_ROOT/services/<service-name>-<short-id>/messaging/` (replaced in place): `messaging.md`, `metadata.json` and optional `architecture-N.d2` files. Repeating the phase produces a new version and does not start subsequent phases.

## Additional documentation

After reviewing Phase 1, select the documents/prompts/model and click **Generate documents**. Requests include the inspected graph hashes. The API and documentation worker check them against current source; changed source or changed folder selection requires regenerating the graph. The worker does not silently generate a replacement graph.

The existing Agents SDK and configured model are retained. Its `code_graph` function tool exposes a bounded overview, paginated symbol search, callers, callees and unresolved references from the selected snapshots. File tools verify behavior, SQL, configuration and dynamic connections. Graph paths are relative to the same code root used by file tools.

The versioned code-analysis fixture covers entry points, end-to-end flows, all discovered state, writers, business transitions, consistency, history/audit changes, outgoing effects and coverage gaps. Custom prompt versions are preserved. No source means an explicit “Code is not available” document and no code-analysis model call.

Code and deployment diagrams are separate documents. The code graph is shared across environments. The deployment diagram is linked to the chosen environment and servers; it reuses inspection findings when a deployment document is generated in the same request. Otherwise it uses only explicitly selected SSH resources. Neither diagram is proof of complete runtime behavior.

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
processing fingerprints: regenerate Phase 1 before generating new documentation.
Existing graph snapshots and documents are retained.

Agent graph queries use CodeGraph's public SDK through `query.cjs`. The SDK opens
a temporary copy because v1.6.0 does not honor its `readOnly` option. Source is
copied into that disposable project only for requested nodes, after verifying
selected-folder boundaries and the indexed content hash. No reindexing, custom
SQL, automatic sync, or model calls happen during these queries. Storage backup
and the existing administrative graph browser still use SQLite; this change
replaces the agent's graph querying and traversal.
