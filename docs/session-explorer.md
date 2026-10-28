# Session explorer

The session explorer reads one agent session as an object with query methods. A session is all of its run records
(`ka.agent_run_events`), one per turn. A person uses it on the run page's **Debug** tab, and a model gets the same
methods as tools (the session reviewer, a later step). The code is in
`services/admin-api/src/knowledge_admin/sessions/explorer.py`.

## Turns and steps

- **Session:** the runs whose keys are `cc:<session_id>:<n>` (`session_worker.run_key`), in turn order. A run outside
  a session (`run:<id>`) is a session of one turn.
- **Turn:** one run key. It holds the person's input, everything the agent did, its final answer (the last message
  with no tool call after it) and its status.
  - The status is the turn's last `end`. It is `unfinished` when there is no end yet, or when a retry continued after
    one.
  - The session's status is the status of its last turn.
- **Step:** one model response inside a turn. A step holds:
  - its inputs: the task, a `comment` sent while the agent worked, a `retry`, or an `expert` or `subagent` task;
  - its reasoning and messages;
  - its tool calls, paired with their results by `call_id`. Each call has a status (`ok`, `error` or `pending`), an
    output text and a size;
  - its errors, its usage and the end it reached.

  A new step starts when an input or a model event (reasoning, message, tool call, usage) follows a step that already
  has its usage or its end. A tool result's status is its `is_error`, or `activity.tool_failed` on its output when the
  event has no `is_error`.
- **Nested runs:** events with a `parent_call_id` belong to an expert call, or to a subagent that `run_subagent`
  started. They become a child run, with its own steps, under the tool call with that id. This is the same link the
  run page uses to open `?call=`. A nested run's status follows its call's result: `completed`, `failed` or
  `unfinished`.

## Loading

`build(rows, run_key=..., meta=..., secrets=...)` is the pure function that turns the rows into the object, so tests
need no database.

`SessionRecord.load(conn, project, key, secrets)` reads every turn once. `key` is either a run key or a session id:

- With a run key, that run's turn is the **focus turn**.
- With a session id, the last turn is the focus turn.

A run key reads its session only when this workspace has a record for that run key. Otherwise, and for an unknown
run, it raises `RunNotFound`.

## Redaction

Every string has its URLs masked first (`http_errors.safe_url` hides user info, queries and fragments). It is then
redacted with the secrets that the session's pinned configuration references (`activity.redact`, with the same
secrets the session Debug tab uses). Records are already redacted when they are written, so this is a second pass.

## Methods

Every method is deterministic and returns JSON. Results are capped at `MAX_CHARS` (24,000) characters of serialised
JSON.

- `total_chars` is always the full size of the result.
- When something was cut, `truncated` is true. List items are dropped first, and `total_items` says how many there
  were. After that, long strings are shortened.

| Method | Returns |
|---|---|
| `overview()` | the session (id, the run key looked at, focus turn, agent, model, status, times); one line per turn (input preview, step count, tools, errors, status, tokens); totals (steps, tool calls by name, tool errors, errors, tokens, expert calls and their tokens) |
| `timeline(kinds, turn, tool)` | the events in order, each with its turn and step; filters by kind, turn or tool name; tool results as status and size only |
| `tool_calls(turn, name, errors_only)` | call id, turn, step, name, arguments, status, output size, `expert_call` and `nested_run` |
| `tool_output(call_id, offset, max_chars)` | one page of one output, found in any turn and inside expert calls; the page is 8,000 characters by default and at most 20,000; returns `total_chars` and `next_offset` (null at the end) |
| `turn(n)` | session turn n: its status, answer and steps; outputs as `{size}`, with a short `error` text for a failed call |
| `search(text, kinds, turn, max_hits)` | case-insensitive substring hits in inputs, reasoning, messages, arguments, outputs and errors, including nested runs: kind, turn, step, call id, `in_call`, count and a snippet |
| `expert_call(call_id)` | the call, the nested run's overview (one line per step) and its timeline |
| `digest(budget_chars, turn)` | the compressed session as **text** (16,000 characters by default) |

### The digest

The digest is plain text, because a model reads it as is and JSON would spend the budget on keys and quotes. It
contains, in order:

- a header line;
- the instructions, taking at most a quarter of the budget;
- per turn, its steps: inputs, reasoning, messages, tool calls as `name(arguments) -> ok|error, N chars` (outputs are
  never included), errors, tokens and end;
- each turn's answer and status;
- the session's end.

When the digest is over budget, it shortens in stages:

1. The first and last turns, the turns with errors and the focus turn stay in full. The focus turn is the `turn`
   argument, or by default the run the digest is read from. As many other last turns as fit also stay in full.
   Every other turn becomes one line, and a note says `N turns shortened; use turn(n) to read one in full`.
2. If it is still over, only the first, last and focus turns stay in full. Their middle steps are then shortened the
   same way: the first step, the last step, failed steps and as many last steps as fit stay in full, and a note says
   `N steps shortened; use turn(n)`.
3. As a last resort, the text is cut at the budget.

The digest returns `text`, `size_chars`, `full_chars` (the size of every record in the session, outputs included),
`focus_turn`, `shortened_turns`, `shortened_steps` and `truncated`.

## Tools

`METHODS` is the single table that holds each method's name, tool name, description and argument model. The
descriptions are written for a model: what each method returns and when to use it.

`tools(record)` makes SDK function tools bound to one session record:

- `record_overview`, `record_digest`, `record_timeline`, `record_tool_calls`, `record_tool_output`, `record_turn`,
  `record_search` and `record_expert_call`.
- None of them takes a `run_key`, so a model cannot read another session.
- Their schemas are strict: every optional argument is nullable, and null means its default.
- They are not in the tool catalog, the tool fixture or the access matrix.

## API

Both endpoints stay on the run key, `/api/agent-runs/{run_key}/explore`, and work on **that run's whole session**.
They have the same access rule as the run record: an administrator, and a record of that run key in this workspace
(404 otherwise). Whoever may read the run record may read its session's records. This is also the rule the session
page uses: admin, same workspace.

- `GET /api/agent-runs/{run_key}/explore` returns `{methods: [{method, tool, description, parameters}], session_id,
  turn}`. The methods are exactly what the model sees, and `turn` is the run key's turn.
- `POST /api/agent-runs/{run_key}/explore` takes `{method, args}` and returns `{result, size_chars, approx_tokens}`.
  - The size is that of the serialised result, and the token count is a quarter of it, rounded up.
  - A bad argument returns a 400 that names it, for example `turn: Input should be greater than or equal to 1` or
    `turn: there is no turn 9; the session has turns 1, 2.`. An unknown method returns a 400 the same way.

## The Debug tab

`/activity/runs/:runKey/debug` sits beside **Record**, which shows the run's events as before. It explores the run's
whole session, with the run's turn preselected: every `turn` argument starts at it.

- At the top, the page shows the digest's size against the full session's size (the compression ratio), with a
  **Show digest** button.
- Below that are a method picker with the method's description, a form generated from the method's schema (text,
  number, a checkbox, and checkboxes for `kinds`) and **Run**.
- The result shows as pretty JSON (the digest as text), with its size and approximate tokens. Clicking a call id in
  a result fills the form with `tool_output` for that call.
- **Tool schema the LLM sees** shows the tool's name, description and schema.
- Errors show inline.
