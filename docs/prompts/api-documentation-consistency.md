Produce the documentation in one agent run using the supplied graph, source
collection and original source. Keep evidence gathering focused on this endpoint.

Establish the ordered processing flow and state effects before writing the
overview and examples. For each behavior, retain its actual conditions: work
before a branch, work inside it, repeated helper calls, and effects that already
committed before a later failure. Distinguish a missing configuration from an
existing configuration whose value disables something. Check downstream callees
before concluding what the caller returns or writes.

Use the detailed processing flow as the authoritative explanation in the final
document. Summaries, examples, troubleshooting and quick-reference tables must
preserve its conditions. Prefer a short cross-reference over repeating a rule
with different wording that drops a qualification. Do not create a separate
execution-table artifact or a second review agent.

Describe the scope of claims explicitly: the whole request including middleware,
the handler, one branch, one transaction, or one requested field. A statement
that one branch does not apply the requested mutation does not establish that
the whole request leaves storage unchanged. A helper error does not identify
which internal statement failed; distinguish possible prior commits accordingly.
Terms such as "only", "always", "never", "unchanged", "atomic" and "unreachable"
need evidence covering the stated scope, or a narrower conditional statement.

Give each example explicit initial state and configuration, then derive its
response and persistent effects from the same documented path. Keep examples
illustrative, not invented observations. Distinguish enforced validation from
intended conventions and from library behavior not established by the source.

Before returning, compare the overview, examples and any quick reference with
the detailed flow. Correct inconsistencies within this run. Preserve unresolved
behavior as uncertainty instead of making an absolute assertion in another
section. A source citation must support the attached claim; the existence of the
quoted function alone is insufficient. Return the complete documentation, not
your working notes or a verification checklist.
