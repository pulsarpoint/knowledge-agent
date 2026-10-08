# Run workspace — design

Date: 2026-10-08. Status: approved in conversation. Step 2a of `2026-10-08-agent-tools-design.md` (Feature 3a).

## Goal

The agent **Test** tab and the **Generate documents** session page become one component, `RunWorkspace`, over one
backend. Today Test chats run inside the admin-api request, with a 300-second limit per turn and Cancel only, and show a
short activity list. Generate documents sessions run in the session worker, with Stop, messages that reach the agent
while it works, and the full event history. After this step both are **sessions run by the worker** and both pages show
the same workspace. Step 2b (`run_subagent`) adds subagents to its sidebar.

User decisions:
- Test chats run in the session worker (one backend, one event history and stream).
- Existing Test chats and their saved suggestions are dropped; their run records stay readable in Agent runs.
- Layout as below; the draft opens in a wide side panel from the sidebar.

Out of scope: subagents (2b); Claude Code agents in the Test tab (their General tab keeps linking to Generate
documents); renaming the `/api/claude-code/sessions` routes.

## Data model (`0081_test_sessions`)

- **`ka.agent_sessions_cc.purpose`** `text NOT NULL DEFAULT 'documents' CHECK (purpose IN ('documents','test'))`.
- **Documentation fields:** `document_name` and `folder` become nullable; a check requires both for `documents` and
  neither for `test`. A `test` session also has no `draft`, `draft_turn` or `document_id` (check).
- **Test sessions are native only:** check `purpose = 'documents' OR runtime = 'openai'`.
- **Usage totals:** new columns `model_calls` and `reasoning_tokens` (`integer NOT NULL DEFAULT 0 CHECK (>= 0)`),
  added per model call with the existing input, output and cached totals, for both purposes.
- **Suggestions:** `ka.agent_session_reviews` is recreated pointing at `agent_sessions_cc(id) ON DELETE CASCADE`
  (columns: id, session_id, actor_id, comment, context, suggestion, response_turn nullable, created_at).
- **Dropped:** the old Test chats. The migration deletes `ka.agent_session_reviews`, `ka.operations_commands`,
  `ka.operations_runs` and `ka.agent_sessions` and drops those tables. Events in `ka.agent_run_events` (run keys
  `run:<id>`) are kept, so Agent runs still shows those records.
- **Down:** recreates the old tables empty and drops the new columns and checks after deleting `test` sessions. History
  is not restored; documented in deploy/README.

## Backend

### Creating and listing Test sessions

- **`POST /api/agents/{agent_id}/sessions`** `{message, run_resources?}` (replaces the old Test run endpoint):
  - the agent must be native and enabled; its current configuration is resolved with the run resources exactly as
    the old Test start did (`run_config`/`resolve`, per-run validation and errors unchanged) and pinned in
    `agent_config`;
  - inserts an `agent_sessions_cc` row with `purpose='test'`, `runtime='openai'`, title = first 200 characters of the
    message, `prompt_snapshot`/`context_snapshot` as for native documentation sessions (no folders), `run_resources`;
  - queues the message as input 1 and returns the session (201). The worker picks it up.
- **`GET /api/agents/{agent_id}/sessions`** lists that agent's Test sessions, newest first (id, title, status,
  created_at, last_activity_at, actor email).
- **`GET /api/claude-code/sessions`** (Generate documents list) returns only `purpose='documents'`.

### Everything else: the existing session endpoints

`GET /sessions/{id}`, `POST …/messages`, `POST …/stop`, `POST …/close`, `GET …/events/history` and `GET …/events`
serve both purposes unchanged. `POST …/document` answers 400 for a Test session. The session detail gains `purpose`,
`model_calls`, `reasoning_tokens`, and for Test sessions `reviews`.

### Worker

- Runs `test` sessions exactly like `documents` sessions (claim, turns, comments between model calls, Stop with the
  30-second grace, 24-hour idle close, leases, concurrency limit). There is no per-turn time limit.
- `finish_turn` leaves `draft` untouched for `test` sessions.
- **Grounding:** at the end of every native turn the runner computes `grounding.result(answer, run_context grounding)`
  and records it, redacted, in the `end` event's payload as `grounding`. The workspace shows the sources under that
  turn's answer.

### Feedback ("Suggest improvements")

- **`POST /api/agents/{agent_id}/sessions/{session_id}/review`** `{comment, turn?}` (Test sessions only):
  - builds the conversation from the session's events: per turn the user's input, the tools called (name and summary)
    and the final message or error; plus the pinned configuration;
  - runs the reviewer as today (`TuningSuggestion`, 240-second limit) and stores the suggestion with `response_turn`.
- **Review and apply** (`agent_reviews.py`, `SuggestionApply`) reads reviews joined to `agent_sessions_cc` by agent;
  otherwise unchanged.

### Other readers

- Agent working check (`profiles_api.WORKING`), agent history counts and **Delete agent with history**: count and
  delete Test sessions through `agent_sessions_cc` (with `cc:` events); the old tables are gone.
- Removed: `start_turn`, the in-request runner path for Test chats, `/api/agents/{id}/runs*`, `expire_sessions` and its
  periodic call, the old session/close endpoints of `agent_sessions.py`, and the Test chat functions in
  `lib/operations.ts`.

## RunWorkspace (UI)

```
┌ Header: agent · type · Resources · status · tokens · tools used ─── [Show config] [History] [⋯] ┐
├──────────────────────────────────────────────────────────────┬───────────────────────────────┤
│ Main pane (scrolls on its own; history pages load upward)    │ Agents in this run            │
│  You: …                                                      │  ● Main agent · working       │
│  ▸ Worked · 12 tool calls · 18k tokens                       │    12 calls · 18k tokens      │
│  Agent: answer (markdown, sources)  [Full record] [Feedback] │───────────────────────────────│
│                                                              │ Test: Suggestions (2)         │
│                                                              │ Documents: Draft · turn 3     │
│                                                              │  [Open draft] [Save document] │
├──────────────────────────────────────────────────────────────┴───────────────────────────────┤
│ Message box (always on screen) · Send · Stop                                                  │
└───────────────────────────────────────────────────────────────────────────────────────────────┘
```

- **Units:**
  - `useSessionEvents(sessionId)` — the event loading now in `ClaudeCodeSessionView`: latest page, earlier pages on
    scroll, live stream after the newest event, session state updates, live tool counts.
  - `groupTurns(events)` — pure: per turn the input, a work block (reasoning, tool calls with results, comments
    delivered) and the final answer with grounding, status and usage.
  - `RunWorkspace` — header, main pane, sidebar, message box; page-specific parts come in as props (header actions,
    sidebar sections, per-answer actions, empty state).
  - `AgentTestView` (Test tab) and `ClaudeCodeSessionView` (session page) become thin wrappers.
- **Header:** agent name, type, Resources mode, status badge, token totals (in, out, cached, model calls), tools used,
  status message (failed/stopped, waiting for the worker, reconnecting).
- **Main pane:** turns as groups: your message; a collapsed **Worked** block ("Worked · 12 tool calls · 18k tokens")
  with thinking, tool calls and results; the agent's answer as markdown with grounding sources; failed or stopped turns
  say so. Earlier pages load when scrolling up; it follows new events while at the bottom, with **Jump to latest**.
  Old Claude Code sessions render through the same grouping.
- **Sidebar "Agents in this run":** one entry, the main agent, with status, tokens and tool calls, updating live (2b
  adds subagents). Below it the page's sections. On narrow screens the sidebar is a drawer opened from the header.
- **Message box:** always on screen. Send works while the agent works (the comment reaches it at its next step) and
  shows the queued/started notice; **Stop** while working.
- **Test tab:**
  - before the first message: the empty state and, for per-run agents, the compact "Resources: … ✎" line opening the
    picker (editable only before the first message); sending creates the session and opens it (`session_id` in the URL);
  - header actions: **History** (this agent's Test sessions), **Show config** (the pinned configuration, or the current
    one before the first message), **New session**, **Close session**;
  - per answer: **Full record** (`cc:<id>:<turn>`) and **Add feedback**; sidebar: **Suggestions (N)** opening the
    feedback panel with **Review and apply**;
  - blocked reasons stay (disabled agent, unsaved changes, closed session).
- **Session page:** back link to Generate documents; sidebar **Draft · turn N** with **Open draft** (wide side panel
  with the markdown) and **Save document** (with the same-name confirmation); **Close session**.

## Errors

| Problem | Behaviour |
| --- | --- |
| Test session for a Claude Code or disabled agent | 409 / 400 with today's messages |
| Resources not allowed / given to a fixed agent | 400 with today's messages |
| Save document on a Test session | 400 "Only documentation sessions have a document" |
| Feedback on a documentation session | 400 |
| Worker not running | "Waiting for the agent session worker…" as on the session page |
| Stream lost | "Reconnecting…", then resumes after the last event |

## Testing

- **Migration:** purpose checks (documents need folder and name, test has none, test is native), new usage columns,
  reviews re-pointed, old tables dropped with events kept; down; rollback pin.
- **API:** create (pinned config, run resources, validation, first input queued), list per agent, documents list
  excludes Test sessions, Save document refused, detail fields.
- **Worker:** runs a Test session turn (no draft change), comments mid-turn, Stop, grounding in the `end` event, model
  calls and reasoning tokens added.
- **Feedback:** conversation built from events (inputs, tools, answers, errors), stored with the turn, Review and apply
  still finds it.
- **Agent history and delete** cover Test sessions; old endpoints return 404.
- **UI:** `groupTurns` unit tests; `useSessionEvents` (paging, live, reconnect) moved with its tests; workspace for both
  pages (header, Worked blocks, answer with sources, sidebar entry live, message box on screen, drawer on narrow
  screens); Test tab create/History/Show config/feedback/suggestions; session page Draft panel and Save document.
