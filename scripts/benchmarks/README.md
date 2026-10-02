# Native Claude documentation benchmark

## One-question handler prototype

`api_handler_step.py` prepares only the first API-analysis question. It locates
the selected handler using the public CodeGraph tool, checks the indexed source,
and uses ast-grep through `code_search`/`scan_source` to extract the complete
handler and explicitly selected type, constant, import and route nodes. It does
not traverse callees or call a model. The selection file identifies the graph
project, handler name/path, and native ast-grep rules for supporting fragments.

Use `docs/prompts/api-handler-contract.md` with the resulting `evidence.json` in
one fresh, tool-free model request. Save the question, evidence, model settings,
answer and token usage for that individual attempt. The September 16 prototype
uses the saved LLM Settings connection and the existing `agents_api.py` recorder;
it does not change production documentation generation.

`check_handler_step.py --evidence evidence.json --answer answer.json --output checks.json`
checks the JSON structure and exact source quotes/ranges. Interpretation still
requires separate review. Edit and retest this question if necessary; do not
automatically analyze its `later_questions` or start a full documentation pass.

For a second, independent verification call, use
`docs/prompts/api-handler-review.md` with the exact original question, unchanged
source fragments and the first answer. Start a fresh request with no tools or
conversation history. Save the review and its usage separately. It reports
source-backed coverage and proposed corrections without rewriting the answer or
starting a later question. This model review is fallible; check its findings
against the source before accepting them.

`claude_api.py` runs one objective through the Python Claude Agent SDK's `query()`.
Claude supplies its native coding system prompt and Bash/Read/Grep/Glob tools. The
script only records events and results; it does not implement an agent loop or
import the application. Production dependencies and documentation jobs are unchanged.

The isolated image pins `claude-agent-sdk==0.2.152` and includes Bash, ripgrep,
Python, SQLite, Git, and Claude's Linux sandbox dependencies.

```sh
docker build -t knowledge-claude-benchmark:0.2.152 \
  -f scripts/benchmarks/Dockerfile.claude scripts/benchmarks
```

Provide `AWS_BEARER_TOKEN_BEDROCK` and `AWS_REGION` in the Docker client's environment.
Pass their **names**, never their values, with `-e`. Do not mount an application
`.env` file or include credentials in the objective. The September 14 comparison
uses the same bearer token as the administration connection `test1`, verified in
memory, and model `eu.anthropic.claude-opus-5` in `eu-central-1`.

Example invocation, with paths chosen for the experiment:

```sh
docker run --rm --read-only \
  --tmpfs /tmp:rw,exec,mode=1777 --security-opt seccomp=unconfined \
  --mount "type=bind,src=$BENCHMARK_SOURCE_SNAPSHOT,dst=$BENCHMARK_SOURCE_PATH,readonly" \
  --mount "type=bind,src=$BENCHMARK_GRAPH_PATH,dst=$BENCHMARK_GRAPH_PATH,readonly" \
  --mount "type=bind,src=$BENCHMARK_OBJECTIVE,dst=/objective.md,readonly" \
  --mount "type=bind,src=$BENCHMARK_RESULTS,dst=/results" \
  -e AWS_BEARER_TOKEN_BEDROCK -e AWS_REGION \
  knowledge-claude-benchmark:0.2.152 \
  --objective /objective.md --source "$BENCHMARK_SOURCE_PATH" \
  --graph "$BENCHMARK_GRAPH_PATH" --output /results/run \
  --model eu.anthropic.claude-opus-5 --effort xhigh
```

Use an absolute source snapshot path and exclude credential files and symlinks
outside the source tree. Verify retained file hashes against the original source.
Mount the snapshot and graph at the paths **already in the Codex objective**, so
the task can remain byte-for-byte identical. Use a fresh results directory per run.
Claude's working directory is writable scratch space in `/tmp`, because its
sandbox needs to create support files there. Source and graph remain read-only.
The container runs as a nonroot user, without host credentials, Docker socket,
previous documents, or other projects mounted. Bash networking is denied by
Claude's sandbox; Bedrock access happens in the SDK process. The nested sandbox
setting is required under Docker; sandbox startup failures do not fall back to
unsandboxed commands.

There is no custom model-call budget, output-length cap, forced finalization, or
retry prompt. Web tools, write tools, MCP, and delegation are not exposed. Project
and user settings are not automatically loaded. Explicitly reading a source
`CLAUDE.md` remains a model-selected source read and appears in the event log.
`xhigh` is the requested effort; the SDK documents a fallback to `high` for models
that do not support it. This is not evidence of equivalent reasoning across models.

Artifacts:

- `objective.md` and `metadata.json`: exact task, hashes, model, SDK, configuration.
- `events.jsonl`: tool arguments/results, public text, and per-call usage; excludes
  private reasoning blocks and redacts the bearer token.
- `result.json`: terminal status, raw SDK usage/model usage, normalized totals,
  timing, and tool counts. Failed runs preserve available events and errors.
- `document.md`: unedited successful final response, with credential redaction only.

Anthropic ordinary input, cache creation, and cache reads are separate counters.
Normalized input is their sum; uncached input is ordinary input plus cache creation.
Aggregate totals prefer `model_usage`, so native compaction is included; raw
main-loop usage is also retained.
Codex's input counter already includes cache reads. Cached input is not free, and
cache writes have separate billing rules: compare token categories before making
cost claims. The SDK's reported dollar amount is an estimate, not an AWS invoice.
Compare raw `model_usage` too, since SDK internal calls may not appear in streamed
main-loop usage. Different models, system prompts, tools, and tokenizers mean this
is a workflow comparison, not an isolated test of SDK overhead.

Reference: [Claude Agent SDK Python API](https://code.claude.com/docs/en/agent-sdk/python),
[Bedrock configuration](https://code.claude.com/docs/en/amazon-bedrock).

## Application Agents SDK comparison

`agents_api.py` imports the application's `agent()` directly. Supply `--config`,
`--objective`, and a new `--output` directory. The configuration contains selected
resource paths and a model credential reference. Resolve the selected LLM Settings
record and its encrypted credential before starting the isolated runner; retain
the model ID and revision in the configuration. Pass the credential in memory via
a process variable named by `--api-key-env` (the existing Bedrock default is
`AWS_BEARER_TOKEN_BEDROCK`). Do not put the credential in the configuration or
artifacts. The runner itself requires no database access. Run it in an
admin-api image with a separate shell worker and shared `/work/agent-workspaces`
volume. Only the runner receives the model key. The shell worker uses the same
read-only source/graph mounts, with `CODE_ROOT` and `CODE_INDEX_ROOT` matching those
absolute paths; its Bash processes have no network access.

The September 15 experiment uses the unchanged Codex objective, the sanitized
source snapshot from the Claude test, and a short code-analysis system prompt.
`get_prompt()` adds the application's normal tool/resource instructions. This
tests the application runtime with the same task; it does not use the production
API phase's longer prompt, JSON task envelope or six-section output validator.
The exact task, assembled system prompt and configuration are saved for review.

The application adapter supplies Claude cache checkpoints and explicit reasoning.
There is no model-turn cap or forced writing call. The benchmark has a one-hour
wall-clock timeout and no automatic compaction. `wire-usage.jsonl` records the
actual Bedrock effort, cache checkpoint count, signed-thinking block count and raw
token counters. It never records thinking text or signatures. `usage.jsonl`
records SDK accounting; `activity.jsonl` records public tool progress, and
`tools.jsonl` retains complete tool calls/results after successful completion.
The final document is saved unchanged. Compare source accuracy as well as cached,
cache-write, ordinary input and output tokens; cache hits are not free.

The DeepSeek comparison reads the `deepseek-flash` OpenAI-compatible Responses
connection from LLM Settings, including its encrypted database credential. It
uses the same task, assembled system prompt, source snapshot, graph and application
tools as the Opus application benchmark. The benchmark configuration requests
`reasoning_effort: max`; this is a provider-specific tier, not an assertion that
reasoning is equivalent to Claude's `xhigh`. It does not change the saved model
record or agent assignments. The read-only SQLite graph access fix made after
the Opus run is included.

For Responses connections, `wire-usage.jsonl` records the requested effort,
reasoning-item count, response status/model, incomplete-output reason, and raw
cached/input/output/reasoning counters. Reasoning contents are passed back to the
provider during tool cycles but are not saved in benchmark artifacts. An incomplete
response does not count as a successful document. No output cap is added; the
provider's own default applies. Prompt caching is automatic at DeepSeek.

## Documentation factual-review experiment

The prompts in `docs/prompts/documentation-fact-review.md`,
`documentation-fact-correction.md` and `documentation-fact-verification.md` are
experimental, not active database prompts. They reuse `agents_api.py` and the
saved LLM Settings connection in separate fresh sessions:

1. Review the existing draft against its source snapshot. Keep known evaluation
   findings outside the agent's accessible folders; provide no previous review.
2. Assess the findings against source before accepting them. Record corrections
   to reviewer mistakes and manually discovered findings separately from blind
   model discoveries.
3. Request exact, non-overlapping text replacements against the original. Validate
   unique matches and accepted finding IDs, then apply them to a new file and save
   a diff. Preserve the original.
4. Verify changed claims in a fresh run. For side effects and transaction counts,
   require an ordered source-backed execution path, including calls before the
   branch. Search the revised document for residual contradictions.

Save each stage's prompt, config, raw output, tools and usage. A valid JSON result,
matching quotation, or in-bounds source citation is not proof of a factual claim.
A reviewer can miss an error or endorse an incorrect claim. Report blind detection
separately from improvements that needed manual intervention; do not label the
whole document verified based on a focused pass.

## Deterministic CodeGraph extraction

`codegraph_api.py` calls the actual `code_graph` function tool. That tool uses
CodeGraph's public SDK (`getNodesByName`, `getCode`, `getContext`, `getCallees`,
`traverse`) via the Node bridge; it does not parse SQLite tables itself. It exports
reachable source, native edges, imports/context, unresolved references, optional
explicit symbol inspections, timing and graph hashes. There are no model calls.
The source lookup checks each file's index hash before returning code. This is
an extraction experiment, not a generated or fact-checked API document.

```sh
python scripts/benchmarks/codegraph_api.py \
  --graph /path/to/graph.sqlite \
  --source-root /path/to/indexed/code/root \
  --source-folder /path/to/selected/project \
  --handler SetPersonalLimits \
  --handler-file controller/personal_limits.go \
  --inspect-name GetPersonalLimits \
  --output /path/to/new/results
```

Run with the admin-api Python environment and `ast-grep` on PATH. `code_search`
also captures the handler's raw Tree-sitter syntax as a comparison against the
indexed edges. The selected handler/file must identify one node. Extra inspected
names are recorded separately; they do not silently repair or expand the call
closure. Handler traversal does not itself inventory router middleware or prove
branch execution. The immutable published graph, or a closed CodeGraph database
with its WAL checkpointed, is required as input.

The September 15 controlled test indexed the same 241-file sanitized snapshot
at two roots using CodeGraph 1.6.0. At the parent directory, it reached 13 nodes
from the handler and misresolved a package call to a controller method. At the
Go module root, it reached 67 nodes (66 functions/methods), resolved the package
call correctly, and included the live-update chain. This motivated the indexing
root fix; no custom Go resolver was needed for that correction. Neither result
proves complete call resolution across unavailable dependencies or dynamic calls.

### Endpoint source collection

Add `--packet` to `codegraph_api.py` to export line-numbered excerpts grouped by
source file, with `packet/index.md` as the model's entry point. Repeated ranges
are merged. The collection contains the handler's native call closure plus
directly referenced constants, variables and data types from SDK `getContext()`.
This includes generated SQL constants when the index links them to query methods.

Use repeatable `--support-function FILE:NAME` for middleware identified at route
registration, and `--source-range FILE:START:END` for registration/build context.
These are explicit inputs, not automatic framework discovery. The collection
does not claim full type closure, SQL schema discovery, dynamic dispatch or
complete external dependency resolution. Zero unresolved references returned for
selected nodes is not evidence that every dependency is resolved.

The exporter records native contexts, selected middleware seeds, source hashes,
ranges and gaps beside the collection. Read-only model access needs only the
`packet` directory and the original selected source/graph. Keep evaluation cases,
previous documents and benchmark results outside the model's accessible roots.
Give the model the index path, not the entire collection pasted into the prompt.
Record any changed task guidance alongside the baseline objective.

### Execution table before prose

`execution_evidence.py` adds a Go call-site syntax index to a source collection.
It uses the existing Tree-sitter scanner and native CodeGraph contexts exported
by `codegraph_api.py`, without a new resolver or manual SQLite queries. It records
call locations, enclosing conditions, defer/callback constructs and graph target
candidates. Lexical order is explicitly not presented as runtime execution order.

The experimental prompts `docs/prompts/api-execution-table.md` and
`api-documentation-from-table.md` split the benchmark into two fresh `agents_api.py`
runs. The first produces steps, paths, state effects and invariants; the second
produces the full document using that table and the same source/tools. Preserve
both raw outputs and all usage, and include both stages in comparisons.

`check_execution_table.py` checks step references and quoted source ranges. These
are mechanical checks, not semantic approval. Review interpretation, path coverage
and the final prose separately; keep evaluation findings and prior documents
outside both runs' accessible folders. Do not silently edit the table or document
to make a benchmark pass. Record any correction pass or manual intervention.

The September 15 DeepSeek trial improved transaction details but retained false
preview and cooldown claims in the final summaries. The two stages used 366,379
uncached input tokens and 204,475 output tokens across 123 calls, compared with
148,866, 52,241 and 40 calls for the graph/source-only trial. All 112 quotations
passed mechanical checks despite semantic errors. This remains an experiment;
the extra model stage is not enabled in the documentation pipeline.

### Consistency within one documentation run

`docs/prompts/api-documentation-consistency.md` is experimental guidance appended
to the original endpoint objective. It keeps one agent run and asks the writer
to derive summaries and examples from its detailed source-backed flow, retaining
conditions and the scope of side effects. Reuse the original source collection,
graph, model and tools; do not supply the model-generated execution table or
earlier answers. The guidance was designed after reviewing previous failures,
so results on the same endpoint are prompt tuning, not a held-out evaluation.

The September 15 single-run trial used 136,147 uncached input tokens and 68,062
output tokens across 30 model calls (5m40s). Missing-cooldown and timezone details
improved, but preview side effects and transaction summaries still contradicted
the source. Keep this prompt experimental; its self-check is not validation.

### Inventory, then individual component analysis

`docs/prompts/api-component-inventory.md` collects relevant source-qualified
functions and their API call sites. It keeps repeated invocations, identifies
supporting code covered by an entry, and records unresolved dependencies.

`prepare_api_components.py` checks the inventory's names and source locations,
then creates one objective per entry using `docs/prompts/api-component-analysis.md`:

```sh
python scripts/benchmarks/prepare_api_components.py \
  --inventory /path/to/inventory-output.md \
  --source /path/to/source \
  --context /path/to/api-context.md \
  --prompt docs/prompts/api-component-analysis.md \
  --output /path/to/new/objectives
```

Run each objective independently through `agents_api.py`, retaining separate
results, source evidence and usage. The component prompt covers decisions, state,
transactions, failures and how callers use the result in the selected API. An
inventory description is navigation data, not a factual conclusion. Mechanical
location checks do not establish complete call resolution or semantic correctness.
Report the inventory cost and every analysis cost together. A linked index of the
component files needs no additional model-generated synthesis.

### Small recursive analysis experiment

`rlm_handler.py` uses the actual `dspy.RLM` module and its Deno/Pyodide sandbox.
The frozen source tree stays in REPL variables. A coordinator selects source
ranges, uses native `llm_query_batched` for individual questions, and combines
their answers. This is an isolated benchmark, not the documentation pipeline.

Install `dspy[deno]==3.3.1` in a separate Python 3.12 environment. The script reads
the model and encrypted credential from LLM Settings through the existing local
admin container; it currently requires an OpenAI-compatible Responses connection.
Supply a sanitized text-only source snapshot and the previously verified
CodeGraph/AST `evidence.json` produced by `api_handler_step.py`:

```sh
python scripts/benchmarks/rlm_handler.py \
  --source /path/to/frozen-source \
  --evidence /path/to/evidence.json \
  --questions /path/to/questions.json \
  --expected /path/to/expected.json \
  --model-id SAVED_LLM_ID \
  --output /path/to/new/run
```

Questions are objects with `id`, `question` and `answer_format`. Expected answers
are a separate map from question ID to exact string value; the agent never sees
that map. The initial scope is six conditional questions about one handler, not
a full endpoint analysis. Defaults are eight REPL iterations and eight subcalls,
with `max` reasoning effort; there is no additional output-token cap.

Artifacts include the exact prompt, runner, source hashes, per-call usage, child
inputs/answers, REPL code/output, final answers, and value/citation checks. Private
reasoning and credentials are excluded. Quote matching checks source locations,
not whether the evidence proves the claim. Review those semantics separately.
Output-shape failures are recorded independently from factual correctness.

Account for coordinator calls and failed attempts as well as child calls. A
successful small-question run does not establish correctness across callees,
transactions, dynamic dispatch, or full documentation, and is not a like-for-like
token comparison with a larger documentation task.

### Whole-service context and initial inventory

`rlm_service.py` loads every active code project linked to a saved service. It
exports source and the complete saved graphs into one `service_context` REPL
variable, then asks the RLM for an initial inventory of APIs, messaging,
background work and managed state. The editable instructions are in
`docs/prompts/rlm-service-inventory.md`. This remains an isolated experiment.

Use the same separate `dspy[deno]==3.3.1` environment described above. The local
Compose admin-api must be running, and linked projects must have current graphs:

```sh
python scripts/benchmarks/rlm_service.py \
  --service-id SAVED_SERVICE_ID \
  --model-id SAVED_LLM_ID \
  --output /path/to/new/service-run
```

Add `--prepare-only` and omit `--model-id` to export and validate the context with
zero model calls. Use `--context /path/to/context.json` instead of `--service-id`
to repeat an experiment against the same snapshot. `--prompt`, `--task`,
`--max-iters`, `--max-subcalls`, and `--reasoning-effort` expose experiment choices.
The coordinator uses DSPy's native XML adapter with Python inside CDATA; child
answers use JSON. This avoids escaping generated Python inside JSON strings.
The defaults allow 20 coordinator iterations and eight child calls. Check
`status.json` for forced final extraction when the iteration limit is reached;
an available delegation tool does not guarantee the model actually uses it.

The exporter uses the existing code-processing file selection and refuses stale
graphs or missing mapped folders. It includes text files such as SQL migrations
even when CodeGraph does not parse them. It verifies file hashes and records
preprocessing exclusions. Credentials are not part of service metadata; source
selection excludes credential files through the existing processing rules.
Source may still contain inline secrets, so use an appropriate source snapshot.

Each file key is `<project-id>/<graph-relative-path>`. This namespace prevents
two linked projects' `main.go` files from overwriting each other; it does not
rename source folders. Graph node IDs stay scoped to their project. Nodes carry
the matching `file_key`; raw edge resolution metadata and unresolved references
are retained. CodeGraph's public SDK reads temporary copies of the saved indexes;
the experiment neither parses SQLite itself nor changes the saved graphs.

Inspect `context-summary.json` before generation and `inventory.md`,
`inventory.json`, `checks.json`, `execution.jsonl`, `calls.json`, and `usage.json`
after it. Execution records survive a failed run. Checks verify output structure
and exact citation locations, not semantic accuracy or completeness. Administration
deployment records are labeled as recorded configuration, not live observations.
This command does not SSH into servers, execute service code, write documents
into the application, or modify the production documentation workflow.

Run the focused offline checks with:

```sh
python -m unittest discover -s scripts/benchmarks -p test_rlm_service.py -v
```

### State inventory with focused factual questions

`rlm_state.py` reuses the frozen service context above. Before any model call,
`rlm_evidence.py` parses PostgreSQL statements with `pglast`, records table
declarations and SQLC query names, and reports unparsed SQL/psql content. It
excludes down-migrations and preserves each declaration's location. Schema-qualified
and unqualified table names remain distinct; this is not a replay of migrations
or a claim about the deployed database schema.

Add `pglast==8.4` to the isolated benchmark Python environment. The read-only Go
tools also require the `ast-grep` CLI (tested with 0.45.3). Production dependencies
and containers do not change.

```sh
python scripts/benchmarks/rlm_state.py \
  --context /path/to/frozen/context.json \
  --model-id SAVED_LLM_ID \
  --output /path/to/new/state-run
```

The supplied fixtures cover 21 factual questions in five small runs: scheduler
SQL, personal-limit SQL, temporary-break SQL, the RealityCheck worker and the
GetAccountStatus function. Each run has its own RLM history, an eight-iteration
default and up to four child calls. The model still has the whole frozen context
as external REPL data. Native RLM tools provide complete source ranges, graph
symbol/caller/callee lookup, AST-verified Go function bodies and parsed SQL
statements. Graph data comes from the original public-SDK export, not SQLite
queries. Go syntax caller scans exclude declarations and comments; candidate
matches do not prove alias resolution or runtime execution.

The instructions in `docs/prompts/rlm-state-questions.md` ask the coordinator to
delegate early, pass complete relevant code, and repair only errors returned by
`validate_answers`. That tool checks missing/duplicate question IDs, vocabulary
and exact citation locations. It has no expected factual answers. The runner
reads `fixtures/rlm_state_expected.json` only after all model calls, separately
scoring exact values against the pinned Responsible Gaming source hash. A
successful quote check does not establish semantic correctness. These are
regression cases derived from observed failures, not an unseen evaluation set.

Use `--prepare-only` without a model ID for zero-cost inventory inspection.
Repeat `--case CASE_ID` to run selected cases, and use `--questions`, `--expected`
and `--prompt` for other experiments. Never reuse this expected-answer fixture to
claim accuracy on another source snapshot. The fixture records 19 distinct table
names; the inventory also retains the 30 schema/name candidates underlying them.
Table coverage is deterministic extraction, not a model accuracy score.

Artifacts contain the inventory and parser gaps, per-case answers, execution
code/output, child prompts/answers, token usage and separate factual evaluation.
The context path and hash are recorded in `manifest.json`; retain that frozen
context beside the run. Private reasoning and model credentials are not saved.
Individual case failures are preserved while the remaining cases continue.

For response-format failures, run one case with `--diagnose-protocol`. The
`protocol.jsonl` artifact records every adapter parse attempt, including failures
hidden by ChatAdapter's JSON fallback. `protocol-calls.json` compares hashes and
serialization shapes of provider message text and DSPy's extracted text, without
saving either response text or private reasoning. Credentials and request headers
are excluded. These diagnostics do not repair replies or change source tools.
`--adapter json` provides a native JSONAdapter control; `chat` remains the default.

The 2026-09-16 personal-limit investigation reproduced invalid formatting both
through DSPy and directly through DeepSeek's Responses endpoint with the same
initial messages and a JSON schema. Simple JSON and minimal RLM controls passed.
Switching adapters, shortening the prompt, and a temporary Python-envelope parser
did not complete the real case. The unsuccessful schema override and custom
parser were removed; their run directories retain source copies for inspection.
Do not interpret absent answers from these failed runs as factual errors.

Use `--step-protocol tool` to test native `execute_python` calls for coordinator
steps. `rlm_native.py` replaces only the RLM step generator; DSPy still manages
the same Pyodide interpreter, source tools, external context, child calls, and
SUBMIT validation. Each request receives the REPL metadata and execution history,
so this does not introduce another session store or execution loop. Tool arguments
must contain exactly one nonempty `code` string in exactly one named function
call. Response prose is never used as executable code. Execution errors remain
visible to the RLM through its normal history.

DeepSeek thinking mode rejects required/named tool selection, so this experiment
offers only `execute_python` with `tool_choice="auto"` and validates the returned
call. A missing, wrong, or multiple call fails before execution. No fallback
converts prose into code. `native-instructions.md`, `native-tool.json` and
`native-protocol.json` record the exact tool setup and call validation. Root and
child usage are combined in the ordinary `calls.json` and `usage.json` artifacts;
raw provider reasoning is excluded. Native tools are opt-in; the original text
protocol remains the default for comparisons.

The personal-limit case completed twice with all four factual answers correct,
matching citations, five validated native steps and one child call per run.
Neither required forced final extraction. This establishes a successful repeated
case, not accuracy across the full service or all 21 regression questions.

```sh
python scripts/benchmarks/rlm_state.py \
  --context /path/to/frozen/context.json \
  --model-id SAVED_LLM_ID \
  --output /path/to/new/native-tool-run \
  --case personal_limit_state --step-protocol tool --diagnose-protocol
```

```sh
python -m unittest discover -s scripts/benchmarks -p 'test_rlm*.py' -v
```

### Fixed evidence → child analysis → validation → submission

Use `--workflow staged` for the controlled version of the focused state/worker
experiment. `rlm_staged.py` performs four ordinary Python stages. It reuses the
same frozen snapshot, source tools, saved model connection and factual evaluator,
but does not run an autonomous RLM coordinator or execute model-written Python.
This is a constrained evidence-analysis workflow, not a claim that the original
RLM now reliably plans or delegates.

1. Collect the complete requested SQL statement or Go function. For functions,
   include graph and AST caller candidates, complete enclosing caller functions,
   build/import headers and the caller scan's scope. Missing/ambiguous definitions
   fail before a model call. Unresolved caller context remains an explicit gap.
2. Make exactly one child-model request with that bundle and
   `docs/prompts/rlm-state-analysis.md`. The full service context and expected
   answers are not passed to this child. The same reasoning effort is retained.
3. Validate the complete response, question accounting, vocabulary, observation
   references and selected source ranges. The child selects fragment IDs and
   line ranges; Python copies the exact source quotations. This eliminates quote
   transcription errors but does not establish that a citation supports a claim.
4. Write `submitted.json` only after validation succeeds. Malformed, incomplete,
   invalid or evidence-requesting responses stop the workflow. There is no
   automatic retry or text-extraction fallback in this mode.

```sh
python scripts/benchmarks/rlm_state.py \
  --context /path/to/frozen/context.json \
  --model-id SAVED_LLM_ID \
  --output /path/to/new/staged-worker-run \
  --workflow staged --case reality_worker --case account_status_worker
```

Each case saves `phases.json`, `evidence.json`, the child's answer text, validation
results, submitted answers and usage. Phase status survives a failed case. The
DSPy Responses output's `text` field is used; private reasoning is excluded.
`--prompt` can override the child prompt. RLM iteration/step-protocol options do
not apply to the fixed workflow; its model-call budget is one per case.

The runner now distinguishes factual results from workflow completion. A fallback
can have correct facts while `completion_path` is `fallback` and
`workflow_completed` is false. Autonomous runs also need a child call to count
as completing the intended delegation workflow. `status.json` separately reports
completed workflows and fallback cases. Prior result directories are unchanged.

### One endpoint, one function per step

`api_staged.py` applies the fixed workflow to a frozen Go endpoint. The selection
fixture identifies the handler, explicit middleware seeds and route source
ranges. Saved CodeGraph call edges define a candidate closure; the collector
checks application function boundaries with ast-grep, embeds referenced types and
generated SQL as supporting source, and records unresolved calls. It does not
infer complete runtime reachability from graph edges or discover all middleware
automatically.

Each application function gets a separate evidence bundle and one child call.
The bundle includes incoming argument expressions and deduplicated AST condition,
switch, defer/callback and loop context. Caller conditions and graph confidence
remain visible; source order is not presented as execution order. Generated SQL
and database boilerplate are included as supporting evidence rather than given
separate model calls. Non-call function references remain explicit boundaries.

```sh
python scripts/benchmarks/api_staged.py \
  --context /path/to/frozen/context.json \
  --output /path/to/new/preparation --prepare-only

python scripts/benchmarks/api_staged.py \
  --context /path/to/frozen/context.json \
  --model-id SAVED_LLM_ID \
  --output /path/to/new/endpoint-run
```

Inspect `plan.json` before running. Repeat `--step STEP_ID` to test selected steps
without synthesis. `--selection`, `--prompt` and `--expected` select other
experiments; expected answers are loaded after the model calls, never included
in their inputs. The default selection is the previously benchmarked personal
limits endpoint and is pinned to its source hash.

`fixtures/api_staged_revoke_exclusion.json` selects a second endpoint in the same
frozen application: external self-exclusion revocation. Its separate expected
fixture checks request validation, fatal versus non-fatal errors, a pool read
inside a transaction-owning helper, and an external account-status request before
SQL commit. Receiver-side persistence remains unresolved. Use both `--selection`
and `--expected` to run it; it uses the same analysis prompt. Passing these two
endpoints is not evidence of generalization to other applications or languages.

A full run synthesizes only after every function step passes mechanical
validation. Synthesis receives the route/handler context, individual findings and
their selected source excerpts. The collector preserves the full entry point and
its supplied route/types/imports, plus every finding and its cited source ranges.
Overlapping source lines are stored once per file; short source IDs replace
repeated file paths in citations. Duplicate citations, original node graph
metadata and uncited incoming caller excerpts are omitted from synthesis. The
original node bundles and answers remain unchanged on disk. Findings are not
summarized, truncated or selected with application-specific rules.

Saved `evidence.json` retains numeric line records for validation. The model sees
the same lines as numbered source text and explicit `available_ranges`, with gaps
left visible rather than filled with unrelated code. A citation across a gap still fails validation, as does
conflicting source text or a citation to an unavailable line. The collector does
not infer transaction ownership from call edges; the synthesis scope notes require
effects to be attributed to their source function. Unknown external behavior and
contradictory findings remain explicit.
The shared prompt in `docs/prompts/api-node-staged.md` uses application-independent
control-flow rules: preserve interleaved operations, branches and exits; attach
callee effects at their invocation points; distinguish asynchronous and cleanup
timing; and compare the final walkthrough with source and focused findings. It
does not prescribe a validation/read/write lifecycle or application-specific
branches. Simple helpers should receive concise answers using only relevant caller
context. The prompt's consistency check is not independent verification, and
structural compaction does not mechanically validate narrative ordering. Function
analysis inputs are unchanged; compacting synthesis does not reduce the cost of
analyzing each helper. Endpoint-specific questions remain regression fixtures,
separate from the reusable instructions.
Documentation steps can return explicit evidence requests without making up
missing behavior. These are recorded as `needs_evidence`, propagated into the
synthesis and displayed in the document with `has_evidence_requests`. A
completed workflow means the stages finished, not that all dependencies or facts
are resolved. The SQL/worker regression workflow still blocks unmet evidence requests.
An invalid step stops the run; completed artifacts remain available. No automatic
retry or fallback is used.

To continue after investigating a failure, pass `--resume-from /path/to/previous-run`
and a new `--output` directory. This revalidates saved responses without model
calls only when the source, model settings, prompt and step evidence are identical.
Only syntactic ancestor-ID ordering is ignored when comparing evidence because
AST scans can return those IDs in a different order. Revalidation retains the
exact original input layout; no source or condition changes are ignored.
Nested observation references are checked against actual keys in the bundle;
unqualified nested keys are accepted only when unique. Ambiguous keys must use
their full path.
An exact `path:start-end` subrange locator can be normalized to its enclosing
fragment only when every selected line was supplied; these normalizations appear
in `validation.json`. Unknown paths, invented IDs and unseen lines still fail.
Use `--rerun-step STEP_ID` explicitly when a new response is needed. Original
artifacts are preserved; each usage row distinguishes new calls from reused calls.
Do not add reused token counts again when calculating the cost across attempts.
For a repeated response-format failure, `--repair-step STEP_ID` sends that step's
previous answer and structural validation errors in one explicit correction call.
It does not send expected factual answers or automatically retry. The full request
is saved in `calls.json`; failed attempts retain their usage in prior directories.

Each directory has evidence, phases, raw answer text, validation, submitted
answers, call usage and status. `document.md` combines the synthesis and linked
function findings; `usage.json` includes every executed step. The factual
evaluation separates answered questions from absent downstream results. The known
regression checks do not grade all open findings or establish completeness.

```sh
python -m unittest discover -s scripts/benchmarks -p test_api_staged.py -v
```

### Source-only API RLM comparison

`api_source_rlm.py` gives the existing DSPy RLM a frozen project's source files
and endpoint seeds, without graph nodes, edges, dependency closures, earlier
answers or evaluation feedback. Source files remain Python workspace variables;
the coordinator searches and reads them selectively and delegates focused work
through `llm_query`. It reuses the native Python tool protocol and obtains both
root and child model credentials from LLM Settings.

```sh
python scripts/benchmarks/api_source_rlm.py \
  --context /path/to/frozen/context.json \
  --selection scripts/benchmarks/fixtures/api_staged_revoke_exclusion.json \
  --expected scripts/benchmarks/fixtures/api_staged_revoke_exclusion_expected.json \
  --model-id SAVED_MODEL_ID --output /path/to/new/source-only-run
```

The source hashes are checked before execution. `model-input.json` records the
exact source-only boundary. The prompt retains the staged analysis's general
behaviour and ordering guidance, but permits autonomous source discovery instead
of following fixed graph-derived function steps. Therefore this comparison holds
the source, model and endpoint questions constant, but changes the collection and
orchestration workflow; it is not a graph-only controlled ablation.

`execution.jsonl` and `trajectory.json` record Python operations and results;
`calls.json` includes child requests/responses and usage for all recorded calls.
The expected answer key is opened only after generation. `evaluation.json` checks
the original targeted values; `validation.json` checks quotation locations, not
semantic correctness. Review the combined `overview.md` against the source too.
Failures and forced extraction are reported as incomplete, and the runner exits
nonzero. No production pipeline or application settings are changed.

After a protocol failure, `--resume-from /path/to/failed-run` rebuilds the Python
workspace from successful steps without spending model calls on them again.
It requires identical inputs, prompt, model and run limits. Child responses can
be reused during replay only when their prompts exactly match the saved request;
a missing match stops the replay. Steps with execution errors or submission are
not replayed. Only new model calls appear in the resumed run's usage; include each
earlier run's usage once when reporting total experiment cost. No failed response
or factual feedback is supplied on resume.

The native Python bridge validates every returned function call and runs their
code in response order in the shared sandbox. Some compatible providers return
multiple calls even when `parallel_tool_calls` is false. Empty responses, duplicate
call IDs, unknown tools and invalid arguments are still rejected before execution.

```sh
python -m unittest discover -s scripts/benchmarks -p test_api_source_rlm.py -v
```

### Bounded source analysis

`api_bounded_source.py` runs separate source-only RLM workspaces for the scopes
listed in a fixture, then makes one synthesis call. The initial fixture separates
handler/middleware behaviour, database state/transactions, and sender-side external
interactions. Every focused endpoint question must belong to exactly one scope;
each scope also produces an open finding, and synthesis answers the original
endpoint questions with a combined walkthrough.

```sh
python scripts/benchmarks/api_bounded_source.py \
  --context /path/to/frozen/context.json \
  --selection scripts/benchmarks/fixtures/api_staged_revoke_exclusion.json \
  --scopes scripts/benchmarks/fixtures/api_bounded_revoke_exclusion.json \
  --expected scripts/benchmarks/fixtures/api_staged_revoke_exclusion_expected.json \
  --model-id SAVED_MODEL_ID --output /path/to/new/bounded-run
```

Scopes define the questions, dependencies outside the task, and the stopping
condition. They do not supply implementation paths or expected answers. Each RLM
can retrieve additional relevant source; all workspaces start fresh and share no
conversation history. `api-bounded-source-rlm.md` contains the generic procedure.
The normal RLM iteration/subcall failure limits remain unchanged from the previous
source-only benchmark; no new model output token limit is added.

Synthesis receives the original handler and route, all scoped findings and their
cited source. The host includes complete enclosing Go functions using ast-grep and
complete cited SQL statements using the existing SQL parser. It does not follow
graph edges or select other symbols by matching names. Repeated source lines are
stored once. The original staged synthesis prompt and citation validator are used.

Each step records its task, source boundary, Python activity, answers, token usage
and evaluation. Root `usage.json` and `total-usage.json` include the step and synthesis
calls. A failed stage stops the workflow and preserves completed artifacts. This
is an isolated benchmark; production documentation behaviour is unchanged.

```sh
python -m unittest discover -s scripts/benchmarks -p 'test_api*source*.py' -v
```

### Function analysis with extracted evidence

`api_function_steps.py` tests one function at a time without an RLM discovery
loop. `function_evidence.py` extracts a complete Go function, its imports and
referenced package declarations using ast-grep. Initial functions are selected
explicitly by file, name and optional receiver. No graph target is consulted.
The generic prompt is `docs/prompts/api-function-unit.md`.

The runner accepts the saved OpenAI-compatible Responses connection or a native
Claude Bedrock connection from LLM Settings. `bedrock_analysis.py` sends the same
system/user text directly to InvokeModel, with adaptive thinking and `xhigh`
effort. It uses the selected model's installed LiteLLM output allowance for the
required `max_tokens` parameter (128,000 for the tested Opus 5 connection). It
does not add an agent runtime, tool loop or automatic retry. Claude is asked for
JSON by the shared prompt; the Responses path also enables JSON-object mode.
Effort labels and tokenizers differ between providers.

Bedrock cache checkpoints are placed on the system and final message blocks.
Normalized input is ordinary input plus cache reads plus cache creation; cached
input counts reads only, and uncached input includes creation. Raw provider usage
and stop reasons are retained in each round's call records. Output includes
thinking, but a separate reasoning counter is unavailable and is recorded as
`null`, not zero. Thinking blocks, signatures and credentials are never saved.
An incomplete native response retains its usage and cannot become a valid result.
See AWS's [request fields](https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-anthropic-claude-messages-request-response.html),
[adaptive thinking](https://docs.aws.amazon.com/bedrock/latest/userguide/claude-messages-adaptive-thinking.html)
and [prompt caching](https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html).

Every uniquely resolved local dependency also includes its exact declaration
signature under `observations.dependencies.<id>.signature`. This exposes declared
parameters and multiple return types without expanding callee bodies. Signatures
have source locations and can be referenced with `observation_ids`; cite the
supplied call site as source evidence. Unknown or ambiguous targets have no
signature. A declared type does not establish implementation behavior.

```sh
python scripts/benchmarks/api_function_steps.py \
  --context /path/to/frozen/context.json \
  --selection scripts/benchmarks/fixtures/api_function_revoke_exclusion.json \
  --expected scripts/benchmarks/fixtures/api_function_revoke_exclusion_expected.json \
  --model-id SAVED_MODEL_ID --output /path/to/new/function-run
```

Each unit starts with one model call. A required missing implementation can be requested
using an exact dependency ID from the supplied call-site inventory. The host
resolves direct functions within the same package or an explicitly imported local
package, extracts them, and makes a fresh call with the expanded evidence.
For a root-scope `value := Constructor(...)`, it can also resolve `value.Method`
when the constructor declares one concrete local struct result and that package
has one matching receiver method. The requested evidence includes the constructor,
receiver declaration, method and referenced declarations such as the SQL constant.
The dependency records the source ranges behind the method selection.

Arbitrary paths are rejected. Reassignment, shadowing, nested initializers,
interfaces, generic constructors, unsupported return types, ambiguous definitions,
indirect calls and unavailable packages remain boundaries rather than selecting a
same-named symbol elsewhere. Method identity does not prove invocation success,
runtime activation, nil safety or unchanged object state. This is a conservative
Go syntax resolver, not a complete compiler/type resolver or a general
cross-language call graph.

`--max-rounds` defaults to three calls per unit, including the initial call. If no
new source can be supplied, or the last call still requests evidence, the unit is
marked `needs_evidence`; it is not silently treated as complete, and exits with
status 2. `--prepare-only`
writes the initial evidence without accessing credentials or making model calls.
No additional output token limit is introduced.

The existing staged validator attaches exact quotations from returned fragment IDs
and line ranges. The model does not transcribe source quotes or generate Python.
The extractor retains whitespace-only gaps between selected ranges in the same
file, so citations can span those blank lines. It does not fill gaps containing
omitted code or comments; the validator still requires every cited line to be
supplied. It never silently expands a citation into an unprovided implementation.
Every round saves its input, raw response, validated answers, requested dependencies
and usage. Top-level totals include all calls. Expected categorical values are
loaded only after generation; review explanations separately. The included fixture
tests a handler and a transaction function, including rollback uncertainty and
literal log numbers. It does not generate a complete endpoint document.

The separate query fixture starts with only `GetSelfExclusion` in
`self_exclusion_m/main.go`. It tests requesting `New(db.DB_POOL)` and the concrete
`Queries.GetSelfExclusion` method, then identifying the SQL actually passed to
`QueryRow`. The same-named scheduler method must not be selected. Its 13 expected
values are a targeted regression for a known failure, not a held-out evaluation.
Unprovided database driver and server behavior remain unknown.

```sh
python scripts/benchmarks/api_function_steps.py \
  --context /path/to/frozen/context.json \
  --selection scripts/benchmarks/fixtures/api_query_self_exclusion.json \
  --expected scripts/benchmarks/fixtures/api_query_self_exclusion_expected.json \
  --model-id SAVED_MODEL_ID --output /path/to/new/query-run
```

`api_function_delete_pending_limit.json` exercises a different endpoint with the
same prompt and resolver: its handler and the two mutation helpers are separate
units. The helpers initially receive only their own bodies and referenced
declarations, then request the actual receiver methods and SQL as needed. The 29
categorical checks cover branch order, error handling, amount conversion and the
SQL changes; three additional walkthroughs need manual semantic review.

```sh
python scripts/benchmarks/api_function_steps.py \
  --context /path/to/frozen/context.json \
  --selection scripts/benchmarks/fixtures/api_function_delete_pending_limit.json \
  --expected scripts/benchmarks/fixtures/api_function_delete_pending_limit_expected.json \
  --model-id SAVED_MODEL_ID --output /path/to/new/delete-pending-run
```

These fixtures use manually selected entry functions and source-reviewed
questions. They test analysis on another endpoint, not automatic endpoint
discovery, complete documentation coverage or generalization to another language.
The prompt requires literal state assignments, signature-backed return types and
walkthroughs consistent with branch exits and individual answers. These are model
instructions, not a mechanical semantic validator: prose still needs source
review, even when every categorical value and citation passes.

```sh
python -m unittest discover -s scripts/benchmarks -p test_function_evidence.py -v
```
