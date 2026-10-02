Complete the supplied API documentation objective using the execution/state table,
source collection and original code. Read the table before writing narrative.

The table is an intermediate model-produced analysis with source quotations. Its
quotation locations were mechanically checked; its interpretations are not a
formal proof. Verify any ambiguous or conflicting behavior against the actual
function/query. Do not silently copy an incorrect table claim.

Use one consistent account of execution across the overview, detailed flow,
database section, transaction section, examples and troubleshooting notes. Preserve
common work before branches, repeated calls, conditional SQL effects and separate
transaction boundaries. Derive statements about side effects from the entire
request path, not merely from the name or final branch of an operation. Distinguish
configuration absence, defaults and explicitly disabled configuration.

Before returning, reconcile every example and broad claim against the table and
source. Statements such as "unchanged", "only", "always", "never", "atomic" and
"unreachable" need coverage of the relevant paths. If a guarantee cannot be
established, describe the known behavior and uncertainty instead. Keep source
citations in the document. No need to expose internal step IDs to the reader.

Return the complete Markdown document; do not return a review or a correction list.
