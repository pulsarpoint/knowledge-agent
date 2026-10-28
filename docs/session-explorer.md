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

- `SessionRecord.load(conn, store, project, run_key)` takes a **run key only**, as the run record does. It reads
  every turn of that run's session once, and the run's own turn is the **focus turn**. It raises `RunNotFound` unless
  this workspace has a record for that run key.
- `SessionRecord.load_session(conn, store, project, session_id)` is for internal callers such as the session
  reviewer. Its last turn is the focus turn. The API never takes a session id.

Call ids are unique in practice (the providers make them). If an id repeats, `tool_output` and `expert_call` take
the first one, and within one run a later call with the same id replaces the earlier one. A call id that repeats
inside its own nested run is not followed, so a loop cannot happen.

## Redaction

Every string, dict keys included, has its URLs masked first (`http_errors.safe_url` hides user info, queries and
fragments). It is then redacted with the secrets that the session's pinned configuration references
(`explorer.session_secrets`, which uses `agents.runner.known_secrets`, the same secrets as the session Debug tab).
Both `load` and `load_session` fetch these secrets themselves, so no caller can leave them out.

The URL pattern stays linear on long text: a scheme starts only where the previous character cannot belong to one,
and is at most 32 characters long. A 200 KB run of letters is cleaned in milliseconds.

This is a second pass only:
- Records are already redacted when they are written, experts' own credentials included.
- A run outside a session has no pinned configuration, so its second pass has no secrets.

A page or snippet boundary can split a placeholder (`[reda` / `cted]`). That is cosmetic: the secret itself was
already replaced.

## Caps and paging

Every method's serialised result is at most `MAX_CHARS` (24,000) characters, nested lists included.
- `tool_output` meets the cap by shrinking its page when escaped characters make the JSON longer than the text.
- The digest has its own budget instead.

Methods that return a list page it, and they all page the same way:
- **Arguments:**
  - `offset` sets where the page starts;
  - `limit` sets the most items on the page, and null means as many as fit;
  - for `turn`, `offset` is a step offset;
  - for `search`, `max_hits` is the limit.
- **Result:**
  - the list holds the items from `offset` onward, in order, as many as fit;
  - `next_offset` is where the next page starts, and null means nothing comes after it;
  - `total_items` is how many items there are;
  - `total_chars` is the size of the whole unpaged result;
  - `truncated` is true when the cap cut the page, or shortened the content of an item too big on its own (its strings
    and nested lists).

A step with hundreds of parallel calls is shortened in `turn`, but `tool_calls` reads every call, page by page.

Long fields are shortened before paging, so they do not crowd the items out of every page:
- fields repeated on every page: a turn's answer and an expert call's answer (2,000 characters), and an expert
  call's arguments (500);
- in `turn`, a step's input, reasoning, message and arguments (2,000 characters).

`search`, `tool_calls` and `tool_output` read the full text.

## Methods

Every method is deterministic and returns JSON.

| Method | Returns |
|---|---|
| `overview(offset, limit)` | the session (id, the run key looked at, focus turn, agent, model, status, times) and totals (steps, tool calls by name, tool errors, errors, `tokens` of its own model calls, `expert_calls`, `nested_runs` (experts and subagents) and `nested_tokens`), then one line per turn (input preview, step count, tools by name, errors, status, tokens), paged |
| `timeline(kinds, turn, tool, call_id, offset, limit)` | the events in order, each with its turn and step; filters by kind, turn or tool name; `call_id` gives the events of one expert call; tool results as status and size only |
| `tool_calls(turn, name, errors_only, offset, limit)` | call id, turn, step, name, arguments, status, output size, `expert_call` and `nested_run` |
| `tool_output(call_id, offset, max_chars)` | one page of one output, found in any turn and inside expert calls; 8,000 characters by default, at most 20,000; returns `total_chars` and `next_offset` |
| `turn(n, offset, limit)` | session turn n (status, answer) and its steps, paged by step; outputs as `{size}`, with a short `error` text for a failed call |
| `search(text, kinds, turn, offset, max_hits)` | case-insensitive substring hits in inputs, reasoning, messages, arguments, outputs and errors, including nested runs: kind, turn, step, call id, `in_call`, count and a snippet; `total_hits` counts every hit |
| `expert_call(call_id, offset, limit)` | the call, the nested run (agent, status, answer) with its totals, and one line per step of it, paged |
| `digest(budget_chars, turn)` | the compressed session as **text** (16,000 characters by default) |

Turn and session `tokens` count the turn's or session's own model calls. Nested runs' tokens are in `nested_tokens`;
the session page's totals add the two.

### The digest

The digest is plain text, because a model reads it as is and JSON would spend the budget on keys and quotes. It
contains, in order:

- a header line;
- the instructions, taking at most a quarter of the budget;
- per turn, its steps: inputs, reasoning, messages, tool calls as `name(arguments) -> ok|error, N chars` (outputs are
  never included), errors, tokens and end;
- each turn's answer and status;
- `End: <status>`.

When the digest is over budget, turns are reduced one form at a time, oldest first, until the text fits. The forms,
from most to least detail:

1. `full`;
2. `steps`: the first, last and failed steps in full, the others one line each;
3. `compact`: the same, with runs of the other steps grouped into one line that says
   `record_turn(turn=n, offset=k)` reads them;
4. `line`: the whole turn as one line;
5. `dropped`: left out, with consecutive left-out turns sharing one line that names
   `record_overview(offset=…)`.

The order of reduction:

1. The middle turns (not the first, last, focus or errored ones) go to `steps`, then `compact`, then `line`, then
   `dropped`.
2. Then the errored turns go through the same forms.
3. Then the first, focus and last turns go to `steps`, then `compact`, then `brief`. In `brief`, only the first and
   last steps stay in full. At most 5 failed steps are listed, each as its error kind and a short reason. The other
   steps are counted in one line, which also gives the count of failed steps not listed.
4. Then the instructions are left out.
5. As the last resort, the first, focus and last turns become `head`: their header line alone, with a note that
   `record_turn(turn=n)` reads them. `truncated` is then true.

Turn 1's, the focus turn's and the last turn's headers, and `End:`, always stay.

After the text fits, the refill pass runs. Turns that were left out come back as one line each, the latest first,
for as long as they fit. A digest of a session with a long focus turn therefore still uses close to its budget. The digest returns `text`, `size_chars`, `full_chars`, `focus_turn`, `reduced` (`{turn: form}` for every
turn not in full) and `truncated`.

`full_chars` is the size of every record in the session, outputs included, without the parts that repeat other fields
(a result's preview, a call's summary).

## Tools

`METHODS` is the single table that holds each method's name, tool name, description and argument model. The
descriptions are written for a model: what each method returns and when to use it.

`tools(record)` makes SDK function tools bound to one session record:

- `record_overview`, `record_digest`, `record_timeline`, `record_tool_calls`, `record_tool_output`, `record_turn`,
  `record_search` and `record_expert_call`.
- None of them takes a `run_key`, so a model cannot read another session.
- Their schemas are strict: every optional argument is nullable, and null means its default.
- They are not in the tool catalog, the tool fixture or the access matrix.
- Their names must be in the run's `context['tools']`, which `runner.agent` sets from the agent's tools. That is the
  backstop that `tools/sdk._function_tool` checks.

## API

Both endpoints stay on the run key, `/api/agent-runs/{run_key}/explore`, and work on **that run's whole session**.
They take **run keys only**: a bare session id returns 404, as it does on the run record.

They have the same access rule as the run record: an administrator, and a record of that run key in this workspace
(404 otherwise). Whoever may read the run record may read its session's records. This is also the rule the session
page uses: admin, same workspace.

- `GET /api/agent-runs/{run_key}/explore` returns `{methods: [{method, tool, description, parameters}], session_id,
  turn}`. The methods are exactly what the model sees. `session_id` and `turn` come from the run key, after a check
  that the record exists, without loading the session.
- `POST /api/agent-runs/{run_key}/explore` takes `{method, args}` and returns `{result, size_chars, approx_tokens}`.
  - Each request loads the session and applies the full redaction, including the second pass.
  - The size is that of the serialised result, and the token count is a quarter of it, rounded up.
  - A bad argument returns a 400 that names it. An unknown method returns a 400 the same way.

## The Debug tab

`/activity/runs/:runKey/debug` sits beside **Record**, which shows the run's events as before. It explores the run's
whole session, with the run's turn preselected: every `turn` argument starts at it.

- At the top, the page shows the digest's size against the full session's size (the compression ratio), with a
  **Show digest** button.
- Below that are a method picker with the method's description, a form generated from the method's schema (text,
  number, a checkbox, and checkboxes for `kinds`) and **Run**.
- The result shows as pretty JSON (the digest as text), with its size and approximate tokens.
  - Clicking a `call_id` fills the form with `tool_output` for that call.
  - Clicking an `in_call` fills the form with `expert_call` for that call.
  - When the result has a `next_offset`, **Next page** runs the method again from there.
  - Changing the method clears the result.
- **Tool schema the LLM sees** shows the tool's name, description and schema.
- Errors show inline, and a failed digest says so.
