Analyze the selected inventory entry thoroughly in the context of the supplied API.
You have one component to analyze, not the whole endpoint to document.

Read the inventory entry, the exact function body, its relevant callers and the
supporting code needed to establish its behavior. Use focused source reads and the
native CodeGraph tools. Inventory labels and graph targets are navigation hints,
not established semantic conclusions. Verify against source, including actual SQL.

Explain:

- How this API reaches the function, at which call sites, with which arguments and
  under which conditions. Keep repeated invocations and distinct caller contexts.
- Every decision relevant to this API: the predicate, values it depends on, which
  branch runs, early returns, and code paths excluded by the API's actual arguments.
- State read and changed, units and transformations, dependent configuration,
  behavior when data/configuration is absent, and downstream side effects.
- Transaction ownership and exact begin/commit/rollback boundaries. Separate a
  helper returning an error from the different internal points that can fail.
- Returned values, errors and how this API's caller uses them to continue, respond,
  or fail. Explain whether effects may already have committed at that point.
- Boundary cases and unresolved external behavior. Cite source file/line evidence.

Follow relevant callees far enough to verify effects attributed to this component.
If another inventory entry owns that behavior, cite its ID and the source boundary
instead of reproducing its entire analysis. Do not assume its future report is
available or correct. Explicitly record any cross-component question that remains.

Return a Markdown analysis with the component identity, API call-site context,
decision table, state/effect and transaction details, returned outcomes, and open
questions as applicable. Small components do not need artificial complexity;
large components need all relevant branches. Scope every claim to the function,
call site or API path it actually covers. Do not generate an endpoint-wide overview,
quick reference or combined documentation, and do not delegate to another agent.
