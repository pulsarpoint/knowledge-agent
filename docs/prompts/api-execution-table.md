Analyze the supplied endpoint's execution and persistent-state behavior before any
prose documentation is written. Return only a compact JSON evidence table.

Start from the extracted source index and call-site syntax index. Read individual
functions and query definitions as needed. The syntax index records lexical
positions and enclosing constructs; it is not a runtime trace. Use the CodeGraph
SDK tool for target candidates, then check the actual package/receiver definition.
Do not load every syntax JSON file into context: use Python to select the relevant
caller and call sites, or inspect the original functions directly.

Build the common pre-branch path first, then each mutually exclusive branch and
its failure exits. Include operations performed by callees before they return.
Account for argument evaluation, callbacks, defer, early returns and error paths.
Keep similarly named methods/functions distinct using file and receiver. A helper's
name is not evidence of whether it reads or writes. Look at actual query statements.
Record transaction ownership, begin/commit/rollback ordering and which effects can
already have committed when a later step fails. Default configuration creation and
computed conditions must be distinguished from existing stored state.

Do not repeat routine logging, data formatting or external library internals in
every row. Use reusable step IDs for the same callee behavior and preserve repeated
invocations in path sequences. The table should be an engineering evidence record,
not a line-by-line code translation or an essay. Unknown dynamic calls, library
semantics and deployment settings remain explicit unknowns.

Return this JSON shape:
{
  "endpoint": "method and route",
  "steps": [{
    "id": "unique step ID",
    "function": "relative/file.go::Receiver.Function",
    "source_order": 1,
    "condition": "exact source condition or explicit description of reachability",
    "operation": "what this step actually does",
    "target": "qualified callee or SQL query name, or null",
    "calls": ["IDs for the relevant callee steps, in verified order"],
    "reads": ["state resources read"],
    "writes": [{"resource": "state resource", "effect": "mutation", "condition": "predicate"}],
    "transaction": "ownership and commit/rollback timing, or none/unknown",
    "evidence": [{"path": "relative source file", "start_line": 1, "end_line": 2,
                  "quote": "short exact source text in that range; no ellipses"}]
  }],
  "paths": [{
    "name": "path name", "condition": "when this path runs",
    "steps": ["step IDs in execution order; repeat IDs when invoked again"],
    "committed_effects": ["conditional state changes that can persist"],
    "failure_effects": ["effects that can remain after an error"],
    "evidence": [{"path": "relative file", "start_line": 1, "end_line": 2, "quote": "exact source"}]
  }],
  "invariants": [{
    "id": "unique invariant ID", "statement": "source-backed rule the final document must preserve",
    "step_ids": ["supporting step IDs"],
    "evidence": [{"path": "relative file", "start_line": 1, "end_line": 2, "quote": "exact source"}]
  }],
  "unknowns": [{"question": "unresolved behavior", "reason": "why the source does not establish it"}]
}

Every step, path and invariant needs a relevant source quotation. Source order is
within a function, not a global lexical sort. Check the paths against the steps
before returning: do not lose pre-branch work, include repeated callees, do not
combine distinct transactions, and do not turn conditional mutations into either
unconditional mutations or unconditional absence of side effects. Supply only
verifiable quotations; valid JSON or a valid line range does not establish truth.
