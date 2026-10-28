# Session explorer

The session explorer reads one agent run's record (`ka.agent_run_events` of one `run_key`, such as `cc:<session>:<turn>`)
as an object with query methods. A person uses it on the run page's **Debug** tab; a model gets the same methods as
tools (the run reviewer, a later step). Code: `services/admin-api/src/knowledge_admin/sessions/explorer.py`.

## The object

`SessionRecord.load(conn, project, run_key, secrets)` reads the events once; `build(run_key, rows, meta, secrets)` is
the pure function that makes the object out of the rows (tests need no database). An unknown run raises `RunNotFound`.

- **Run:** agent, model (the session's), user, status (the last end; `unfinished` with no end yet, or when a retry
  continued after one), start and end times, the instructions and the final answer (the last message with no tool call
  after it).
- **Turns:** a turn is one model response and what led to it: its inputs (`task`, a `comment` sent while the agent
  worked, a `retry`, an `expert` or `subagent` task), reasoning, messages, tool calls paired with their results by
  `call_id` (status `ok`, `error` or `pending`; output text and size), errors, usage and end. A new turn starts when an
  input or a model event (reasoning, message, tool call, usage) follows a turn that already has its usage or its end.
  A tool result's status is its `is_error`, or `activity.tool_failed` on its output when the event has none.
- **Nested runs:** events with a `parent_call_id` are an expert call (or a subagent that `run_subagent` started) and
  become a child record, with its own turns, under the tool call with that id, as the run page reads `?call=`. A
  nested run's status follows its call's result (`completed`, `failed`, `unfinished`).
- **Redaction:** every string has its URLs masked (`http_errors.safe_url`: user info, queries and fragments hidden)
  and is then redacted with the secrets the session's pinned configuration references (`activity.redact`, the same
  secrets the session Debug tab uses). Records are already redacted when written; this is a second pass.

## Methods

Every method is deterministic and returns JSON. Results are capped at `MAX_CHARS` (24,000) characters of serialised
JSON: `total_chars` is the full size and `truncated` is true when something was cut (list items first, `total_items`
saying how many there were, then long strings).

| Method | Returns |
|---|---|
| `overview()` | run metadata, one line per turn (input preview, tools, errors, end, tokens), totals (tool calls by name, tool errors, errors, tokens, expert calls and their tokens) |
| `timeline(kinds, turn, tool)` | the events in order with their turn, filtered by kind, turn or tool name; tool results as status and size only |
| `tool_calls(turn, name, errors_only)` | call id, turn, name, arguments, status, output size, `expert_call` and `nested_run` |
| `tool_output(call_id, offset, max_chars)` | one page (default 8,000, at most 20,000 characters) of one output, also of a call inside an expert call: `total_chars`, `next_offset` (null at the end) |
| `turn(n)` | one turn in full; outputs as `{size}`, with a short `error` text for a failed call |
| `search(text, kinds, max_hits)` | case-insensitive substring hits in inputs, reasoning, messages, arguments, outputs and errors (nested runs too): kind, turn, call id, `in_call`, count and a snippet |
| `expert_call(call_id)` | the call, the nested run's overview and its timeline |
| `digest(budget_chars)` | the compressed run as **text** (default 16,000 characters) |

The digest is plain text, because a model reads it as is and JSON would spend the budget on keys and quotes. It has a
header line, the instructions (at most a quarter of the budget), then per turn its inputs, reasoning, messages, tool
calls as `name(arguments) -> ok|error, N chars` (outputs never), errors, tokens and end, then the final answer and the
end. Over budget, the first turn, the turns with errors and as many last turns as fit stay in full; the others become
one line each and a note says `N turns shortened; use turn(n) to read one in full`. If that is still over, only the
first and last turns stay in full, and as a last resort the text is cut at the budget. It returns `text`, `size_chars`,
`full_chars` (the size of the whole record, outputs included), `shortened_turns` and `truncated`.

## Tools

`METHODS` is the one table of method name, tool name, description (written for a model: what it returns and when to
use it) and argument model. `tools(record)` makes SDK function tools (`record_overview`, `record_digest`,
`record_timeline`, `record_tool_calls`, `record_tool_output`, `record_turn`, `record_search`, `record_expert_call`)
bound to one record, without a `run_key` argument, so a model cannot read another run. Their schemas are strict: every
optional argument is nullable, and null means its default. They are not in the tool catalog, the tool fixture or the
access matrix.

## API

Both need what reading the run record needs: an administrator, and a record of that run in this workspace (404
otherwise).

- `GET /api/agent-runs/{run_key}/explore` → `{methods: [{method, tool, description, parameters}]}`: exactly what the
  model sees.
- `POST /api/agent-runs/{run_key}/explore` with `{method, args}` → `{result, size_chars, approx_tokens}`; the size is
  that of the serialised result, tokens are a quarter of it rounded up. A bad argument is a 400 naming it (`turn: Input
  should be greater than or equal to 1`, `turn: there is no turn 9; the run has 3 turns.`), as is an unknown method.

## The Debug tab

`/activity/runs/:runKey/debug`, beside **Record** (the run's events, as before). At the top, the digest's size against
the full run's (the compression ratio) with **Show digest**. Then a method picker with the method's description, a
form generated from its schema (text, number, a checkbox, checkboxes for `kinds`), **Run**, and the result as pretty
JSON (the digest as text) with its size and approximate tokens. A call id in a result fills the form with
`tool_output` for that call. **Tool schema the LLM sees** shows the tool's name, description and schema. Errors show
inline.
