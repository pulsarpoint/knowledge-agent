Analyze ONLY the selected component and its owned supporting code in the context
of the supplied API. Produce a source-cited Markdown component reference.

The inventory is an unverified navigation plan. Verify the selected call sites and
actual import/receiver targets. Use code_graph for all graph queries; use Bash,
Python, rg and focused file reads for source. The raw graph database is unavailable
to shell commands. Read a complete relevant function once, then query specific
missing context. Do not repeatedly dump large files or the entire source packet.

Keep ownership boundaries explicit. Do not reconstruct the whole middleware chain
or re-analyze another component. For calls crossing a boundary, record the exact
arguments, returned values/errors used here, and the owner ID. If a local claim
requires inspecting a callee's SQL or implementation, inspect it, but report only
the fact needed here. External dependency behavior not present in source remains
unresolved. Do not treat names, comments, inventory descriptions, or absent graph
edges as proof of behavior.

Write these sections:
1. Scope and API entry paths: local caller sites, concrete argument expressions,
   guards and exits. Separate mutually exclusive paths and preserve captured
   values passed between calls. Include a short source quote for consequential
   path conditions. A later call is reachable only if earlier calls/guards permit
   it. Treat callback creation and invocation separately.
2. Decisions and effects: one compact table of relevant local branches, predicates,
   reads, writes, return values and errors, citing function/SQL lines. Distinguish
   an attempted operation from one that succeeded. Give transaction begin/write/
   commit/error boundaries where this component owns them. Do not infer row
   mutation from a helper's name or a transaction attempt.
3. Boundary contract: what callers can rely on at each return, what may already
   have happened, which dependencies remain unresolved, and which other component
   owns further analysis. Distinguish request arguments, stored state, derived
   defaults and concurrent changes.

Be detailed about THIS component's consequential behavior; omit generic framework
tutorials, exhaustive field catalogs for unrelated types, repeated descriptions
of other components, whole-API examples and a second prose retelling of the table.
Do not sum calls from mutually exclusive paths or make blanket whole-API claims
from local evidence. When a predicate or fallback differs by call site, state the
difference precisely. Read the final response once for contradictions between its
table and prose, then return it. Do not write a combined endpoint document or read
previous generated component reports.
