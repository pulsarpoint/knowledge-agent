# Agent execution status

Specialist **Test** conversations run the selected agent directly using the official
Agents SDK. It loads the agent's saved prompt, model, enabled capabilities, resource
selections and SSH overrides, then decrypts only their required credentials.
Test chats and live runs refuse Claude Code agents with `Claude Code agents run in Claude Code sessions. Open the agent’s Test tab to start one.`

- `POST /api/agents/{id}/runs` accepts a task, optional UUID request
  ID, optional `session_id`, and optional `is_test` for a new conversation. The first message creates a session; follow-ups
  continue its saved Agents SDK history. The UI keeps `session_id` in its URL. The route
  streams `started`, `activity`, and `completed` events. The final run has
  status `completed`, `failed`, or `cancelled`, with its answer or error.
- Session and run history retain configuration snapshots, conversation messages,
  public activity, result and usage. Full SDK tracing uses
  Phoenix. Known credentials are redacted from persisted and streamed output.
- `POST /api/agents/{id}/runs/{run_id}/cancel` cancels the active turn,
  leaving its session open. Disconnecting the browser does not cancel execution;
  reopening the URL restores messages and polls any running turn. Interrupted
  turns are marked failed and are never automatically retried.
- A disabled agent, missing prompt, missing/disabled model or unavailable
  credential cannot start a test. Duplicate
  request IDs are rejected rather than executing the same task again.

Sessions remain active until **Close session** or one hour without a message or
tuning review. Running turns do not expire. A session allows one turn or review
at a time. Every turn uses the configuration captured when its session began. Test changed
instructions in a new session; saving the agent never changes an existing conversation. **New session** starts fresh
context; it does not implicitly close another active session.

- `POST /api/agents/{id}/sessions` creates a session without starting a model.
  Accepts an optional UUID `id`, `title`, and `is_test` (defaults to
  false). The Test tab marks new sessions with `is_test=true`. Both explicit
  creation and first-message creation call `agent_sessions.create_session()`.
- `GET /api/agents/{id}/sessions` lists saved conversations. Filter with
  `?is_test=true` or `?is_test=false`; the Test history requests only tests.
- `GET /api/agents/{id}/sessions/{session_id}` restores messages and tuning reviews.
- `POST /api/agents/{id}/sessions/{session_id}/close` closes a session and stops
  its current turn. Closed/expired transcripts remain available for review.
- `POST /api/agents/{id}/sessions/{session_id}/review` accepts `{ "comment": "..." }`.
  An optional `response_id` focuses feedback on one response from this session.
  A separate Agents SDK reviewer uses the same model with all capabilities disabled.
  It receives the pinned session configuration, all stored turns with
  their configuration/activity, and administrator feedback. It saves Markdown
  suggestions and structured instruction replacements without changing settings. Proposed memory notes must be reviewed
  and saved explicitly as instructions or skills; no automatic memory is added.

The Test UI is a chat timeline with a persistent composer. Enter sends and
Shift+Enter adds a newline. **Add feedback** beneath a response opens the optional
review panel. **Suggestions**, **History** and **Show config** open side panels;
ordinary chatting does not require a review. The conversation renderer is
`packages/chat-ui/Conversation.tsx`.

`ka.agent_sessions` is the common table for live and test conversations. It stores
the owner, pinned configuration, timestamps, status, `is_test`, and complete
SDK `history`, including tool calls and results. The flag is metadata, not a
separate runtime or permission system; it cannot be changed by a follow-up request.
The session API loads history and commits `run.to_input_list()` atomically with a
successful turn. Failed, cancelled, or interrupted turns leave the previous history
intact. Closing or expiring a session retains its transcript and SDK history.

`agent()` receives `history` as an argument and performs no session-file I/O. Its
temporary workspace is only for tool execution. Credentials remain in backend
memory and are not persisted in history. Session endpoints are mounted
behind the admin gateway.

Migration 36 renames `agent_test_sessions`, marks existing rows as tests, and
preserves their IDs, runs and reviews. Before restarting
the API, import any remaining full history files with
`scripts/migrate_agent_session_history.py --root /data/docs-wiki/agent-sessions`
using the application's PG environment. The importer rejects stale files and keeps
source files as a backup. For sessions whose files were already removed on expiry,
the migration restores the saved user/assistant transcript. Downgrade is blocked
once sessions contain live data or history that the old schema cannot preserve.

### Grounded answers

With Documents on and **Answer only from documents** on, an agent reads its granted
collections through `search_documents` and `read_document` (no file/shell tools for
documents), cites claims with `[n]`, and calls `report_not_covered` to mark uncovered
questions **not covered**. Try it in the agent's **Test** tab.
Migration 0071 removed the former Reader application's conversations and its
`ka.reader_settings`, `agent_sessions.channel` and `agent_sessions.hidden_by_owner_at`.

**Upgrading.** Migration 0060 turns **Answer only from documents** on and Code
(repositories) off for the Search agent; its down migration does not turn Code back
on. Saving the agent's settings changes what runs next; conversations already started keep the configuration they began with.

The former code-agent service and its "Ask the code" escalation were
removed; agents read code with their own tools (`list_files`, `read_file`,
`code_search`, `ast_grep`, `code_graph`).

`knowledge_admin.agents.agent()` runs a configured specialist with the Agents SDK.
The caller provides a saved config, task, history, and decrypted credentials. Responses and Chat Completions
connections use the corresponding SDK model classes; native Anthropic/Bedrock uses
`LitellmModel`. See [the runtime documentation](../services/admin-api/README.md).

Git synchronization, Jira import/export, RAG processing/search and connection tests
are functional integrations. Connection-test buttons may contact their configured
server or model. Keycloak authentication and Phoenix infrastructure remain.

See [agent configuration](agent-profiles.md), [SSH settings](server-operations.md),
[LLM connections](llm-settings.md), and [PostgreSQL migrations](migrations.md).


## Tools

Tools are part of an agent's configuration (`config['tools']`, edited on the Tools tab, see
[agent configuration](agent-profiles.md)). Conversations saved before migration 0078 use the tools
their capabilities gave. The model gets exactly the selected tools whose needs are met; MCP tools are filtered the
same way and edited MCP usage is added to the prompt as `Tool notes:`. `code_graph` is offered when a selected code
project has a current generated graph (`code_graph` results carry its `generated_at`; if the project was synced after
the graph was generated, the answer carries a note to regenerate it or confirm with `read_file`).

Tool usage per run is recorded as counts: the **Tools** column of Agent runs (top three and `+n`, the tooltip lists
all), the run record, the session page and Test chats show a line such as `code_graph 12 · read_file 31 · shell_run 4`
or `No tools used`. A Claude Code session page counts live: the session detail returns its counts with the id of the
last tool call they include, and the page adds the streamed tool calls after it (a replay after a reconnect counts
once; earlier activity loaded by scrolling up is already counted). Test chats refresh their counts with the chat.

## Clones

An agent has **one configuration**: what its tabs show is what runs. There are no configuration versions, drafts or
activation. To try a change, work on a clone: clone the agent, change and test the clone, then apply it to the
original, keep it as an alternative, or delete it. A conversation pins the agent's configuration when it starts, so
saving the agent never changes a conversation in progress.

**Clone agent** (the header menu *Actions for <name>* on the agent page, or the **Clone <name>** button in the All
agents list) creates a new agent with an identical configuration: prompt, instructions, model, type, resources mode,
tools with their usage text, skills, documents, code projects, services, SSH servers (per-server instructions and
connection overrides) and MCP selections. The name is `<name> (copy)`, then `(copy 2)` and so on while taken. Saved
SSH credentials are **referenced**, not copied or decrypted. Sessions, runs, run records, reviews and audit history
are not copied. The clone records `cloned_from` and shows *Clone of <original>* on its General tab and in the list.

- `POST /api/agents/{id}/clone`: creates the clone and returns it (409 `An agent with this name already exists.`
  if a concurrent clone took the name).
- `GET /api/agents/{id}/apply`: for a clone, the sections that differ from its original (`Prompt`, `Instructions`,
  `When to use`, `Model`, `Agent type`, `Resources`, `Tools`, `Skills`, `Documents`, `Code projects`, `SSH`, `MCP`,
  `Services`), the original's revision and a fingerprint of its configuration. 404 `This agent is not a clone.` when
  the agent is not a clone or the original was deleted.
- `POST /api/agents/{id}/apply`: **Apply to <original>** replaces the original's configuration with the clone's in
  one transaction. Everything is copied except name, description, `enabled` and `cloned_from`. It needs the revision
  and fingerprint from the `GET`; 409 `The original changed; review again.` if either moved, and 409 `The original
  agent is working right now. Wait for its documentation sessions and runs to finish, then apply.` while the original
  has a documentation session or run working. It bumps the original's revision and writes the audit event
  `agent.config.applied`. The clone then offers **Delete this clone**.

Saving SSH, per-server or MCP settings locks the agent for the save, so it is serialized with Apply and with other
saves.

**Add feedback → Suggest improvements** stays. **Review and apply** on a suggestion opens *Apply suggestions*: the
proposed instruction and prompt replacements can be edited, then applied with **Apply to this agent** (new
conversations use it) or **Apply to a new clone** (clone first, then write them there).

- `GET /api/agents/{id}/reviews/{review_id}`: the suggestion with the agent's current texts and its fingerprint (404
  `Suggestion not found for this agent.`).
- `POST /api/agents/{id}/reviews/{review_id}/apply`: `{target: 'agent' | 'clone', revision, fingerprint, changes}`; 409 `The agent changed since
  you opened the suggestion. Review it again.`, 422 `Instructions refer to a server outside this agent.`, 400 `Enter a
  system prompt.`

Snapshots of running conversations store credential references, never private keys or provider tokens. Credentials
are resolved at execution time; disabled agents/models/SSH/MCP resources still block use. File contents and external
server state are live; a removed credential or moved folder can make an old conversation unavailable.

Migrations `0079_agent_system_prompt` and `0080_clone_agents` removed agent versions; see
[PostgreSQL migrations](migrations.md) and the upgrade notes in `deploy/README.md`.
