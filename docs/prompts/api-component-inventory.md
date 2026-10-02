Build an inventory of the important functions that determine the supplied API's
behavior. This is the collection phase, not the detailed analysis phase.

Start with the deterministic symbol catalog, registration, handler and configured
middleware. Use graph relationships and focused source reads to identify the
relevant functions, including callbacks or interface implementations missed by
the indexed call graph. Keep unavailable dependencies explicit.

Create one entry per source-qualified function that controls a meaningful decision,
validation, identity/authorization rule, state read or mutation, transaction,
configuration/default resolution, audit effect or response transformation. Keep
similarly named functions in different files/receivers separate. Generated query
adapters, plain constructors and trivial conversions can be inspected as supporting
code by the owning entry; they do not each need an independent analysis. Record
what is covered that way rather than silently discarding it.

For each entry collect its definition and the call sites that connect it to this
API. Preserve repeated call sites. Record only conditions directly visible in the
caller; do not infer a complete execution path or conclude whether storage changes.
A returned callback is not executed merely because its factory is called.

Return JSON only:
{
  "endpoint": "METHOD /route",
  "entries": [{
    "id": "short-unique-lowercase-slug",
    "function": "Receiver.Function or Function",
    "file": "relative/source/file",
    "start_line": 1,
    "end_line": 2,
    "reason": "brief reason this function needs analysis for this API",
    "callsites": [{"file": "relative/caller/file", "line": 1,
                   "caller": "qualified caller", "condition": "visible condition or unknown"}],
    "supporting_symbols": ["relative/file::function or query to inspect with this entry"]
  }],
  "covered_without_separate_analysis": [{
    "function": "relative/file::name", "covered_by": "entry-id", "reason": "why"
  }],
  "unresolved": [{"location": "file:line or dependency", "question": "what remains to locate"}]
}

This list is the work plan for the next phase. Do not write complete branch tables,
state-transition explanations, API examples, broad invariants or documentation.
Do not review earlier generated documentation. Do not delegate. Return the inventory
as your final response; there is no need to construct it through shell scripts.
