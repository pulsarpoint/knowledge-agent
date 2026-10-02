Collect the components needed to document the supplied API. This first pass is a
source navigation plan, not a complete analysis or an API document.

Locate registration, the handler, and middleware through CodeGraph and focused
source reads. Use only the code_graph tool for graph queries. The symbol catalog
and call-site syntax are candidates and navigation aids, not verified reachability.

Group functions into cohesive REQUEST-TIME responsibilities. One component owns
its primary function and the closely related helpers, generated queries, types,
constructors and conversions needed to explain it. Split a component when it has
a separate caller-visible contract or a genuinely different state lifecycle.
Do not create separate tasks for every getter, formatter, UUID converter, startup
constructor, or adapter. Assign shared helpers to one owner and cross-reference
that owner. The same helper may have multiple call sites; preserve those sites.

Keep the plan small through ownership, not by omitting consequential behavior.
Collect identity/access decisions, validation/dispatch, state operations, defaults,
response construction and externally visible effects when present. Startup code
is supporting context only unless the request actually invokes it.

For each component, record the caller's exact argument expressions and directly
visible guards. Preserve early returns/aborts, mutually exclusive alternatives,
callback boundaries, and whether multiple calls use the same captured value.
An if statement's lexical ancestry alone does not prove reachability. Quote the
small relevant source fragment and its location rather than inventing a semantic
condition. Unknown cross-function conditions remain unknown. Do not calculate
whole-request call counts, state transitions or transaction guarantees here.

Return JSON only, with this structure:
{
  "endpoint": "METHOD /route",
  "entries": [{
    "id": "short-unique-lowercase-slug",
    "function": "Receiver.PrimaryFunction or PrimaryFunction",
    "file": "relative/source/file",
    "start_line": 1,
    "end_line": 2,
    "reason": "request-time responsibility to inspect, without an asserted outcome",
    "callsites": [{
      "file": "relative/caller/file", "line": 1, "caller": "qualified caller",
      "condition": "short source quote or unknown",
      "arguments": "exact source argument expressions",
      "path_notes": "visible preceding exit, branch alternative, or deferred/callback boundary; cite lines"
    }],
    "supporting_symbols": ["relative/file::owned function, type or query"],
    "depends_on": ["another-component-id"],
    "scope": "what this component must explain; other components own their own contracts"
  }],
  "covered_without_separate_analysis": [{
    "function": "relative/file::name", "covered_by": "entry-id", "reason": "ownership"
  }],
  "unresolved": [{"location": "file:line or dependency", "question": "what remains to locate"}]
}

Stop when the request-time responsibilities and their source locations are
assigned. Do not fully analyze each component to construct this list. Do not read
previous generated documentation, delegate, or construct the JSON through shell
scripts. Return the plan directly as the final response.
