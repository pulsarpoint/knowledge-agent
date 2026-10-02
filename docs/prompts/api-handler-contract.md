Question 1: What inputs does this endpoint receive, and what does the supplied handler itself do to validate and transform them?

Answer only from the supplied source fragments. Describe direct field reads, assignments, predicates and early returns in this handler. Distinguish explicit handler checks from declared validation tags. A tag's runtime behavior is unresolved when its implementation is absent.

Input entries are caller-supplied API values. Do not list generic framework context objects or helper return values as request fields. Group each logical validation or transformation with its assignments and error/return behavior; do not make separate entries for every line or log statement.

Every called function or method is an opaque boundary in this step, including validators, conversions, getters and framework helpers. State its arguments and how the handler uses its result when relevant to inputs, but do not infer its implementation, storage effects, units conversion, defaults, authorization behavior or error response format from its name. Refer a consequential missing detail to a later question. Do not follow graph edges or document the whole endpoint lifecycle.

Keep expressions literal and preserve the order of relevant checks. Distinguish missing/null from false and zero. A numeric error argument is not an established HTTP status. Output plain JSON with this structure:

Preserve intervening calls and error-return gates between input checks or transformations, even when their implementations are outside this step. Describe their arguments and local control flow as opaque prerequisites. Do not imply two input operations are adjacent or that a later check is always reached when an intervening call can return early.

{
  "endpoint": "METHOD /path",
  "inputs": [{
    "location": "path/header/body/context",
    "name": "field name",
    "source_type": "type as declared, or unknown",
    "local_handling": "what this handler directly establishes",
    "evidence": [{"source_id": "S1", "start_line": 1, "end_line": 2, "quote": "exact short source substring"}]
  }],
  "rules": [{
    "condition_or_operation": "exact source expression",
    "effect": "local assignment, branch, call or return; no hidden callee behavior",
    "evidence": [{"source_id": "S1", "start_line": 1, "end_line": 2, "quote": "exact short source substring"}]
  }],
  "later_questions": [{
    "call": "function/method called by this handler",
    "arguments": "actual arguments or receiver",
    "question": "input-contract detail requiring its implementation",
    "evidence": [{"source_id": "S1", "start_line": 1, "end_line": 2, "quote": "exact short source substring"}]
  }]
}

List rules in source order. Keep later questions to dependencies needed to establish the input contract. Do not guess answers to them. Include evidence for every entry; quote only within the cited lines of its supplied source fragment.
