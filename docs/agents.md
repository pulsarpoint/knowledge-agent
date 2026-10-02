# Agent execution status

The [multi-agent board](agent-board.md) uses an Agents SDK coordinator to delegate
to configured Agents SDK specialists, storing their results and a combined answer.
The coordinator is a protected record in **All agents**. Its **General** tab edits
identity, model, notes, and limits; **Prompts** manages its draft/active system
prompt versions. Its execution routes use `/api/agent-board/tasks`.

Specialist **Test** conversations run the selected agent directly using the official
Agents SDK. It loads the saved active prompt, model, enabled capabilities, resource
selections and SSH overrides, then decrypts only their required credentials.
The coordinator is not involved in a direct test.

- `POST /api/agents/{id}/runs` accepts a task, optional UUID request
  ID, optional `session_id`, and optional `version_id` and `is_test` for a new conversation. The first message creates a session; follow-ups
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
- A disabled agent, missing active prompt, missing/disabled model, unavailable
  credential, or coordinator profile cannot start a specialist test. Duplicate
  request IDs are rejected rather than executing the same task again.

Sessions remain active until **Close session** or one hour without a message or
tuning review. Running turns do not expire. A session allows one turn or review
at a time. Every turn uses the configuration captured when its session began. Test changed
instructions in a new session; activation never changes an existing conversation. **New session** starts fresh
context; it does not implicitly close another active session.

- `POST /api/agents/{id}/sessions` creates a session without starting a model.
  Accepts an optional UUID `id`, `title`, `version_id`, and `is_test` (defaults to
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
ordinary chatting does not require a review. The conversation renderer is shared
with the multi-agent board through `packages/chat-ui/Conversation.tsx`.

`ka.agent_sessions` is the common table for live and test conversations. It stores
the owner, pinned configuration/version, timestamps, status, `is_test`, and complete
SDK `history`, including tool calls and results. The flag is metadata, not a
separate runtime or permission system; it cannot be changed by a follow-up request.
The session API loads history and commits `run.to_input_list()` atomically with a
successful turn. Failed, cancelled, or interrupted turns leave the previous history
intact. Closing or expiring a session retains its transcript and SDK history.

`agent()` receives `history` as an argument and performs no session-file I/O. Its
temporary workspace is only for tool execution. Credentials remain in backend
memory and are not persisted in history. Session endpoints are currently mounted
behind the admin gateway; Reader conversations are described below.

Migration 36 renames `agent_test_sessions`, marks existing rows as tests, and
preserves their IDs, runs, reviews, and agent-version references. Before restarting
the API, import any remaining full history files with
`scripts/migrate_agent_session_history.py --root /data/docs-wiki/agent-sessions`
using the application's PG environment. The importer rejects stale files and keeps
source files as a backup. For sessions whose files were already removed on expiry,
the migration restores the saved user/assistant transcript. Downgrade is blocked
once sessions contain live data or history that the old schema cannot preserve.

### Reader conversations

Reader chat uses the same session store. A Reader conversation is a
`ka.agent_sessions` row with `channel = 'reader'`, a 7-day idle timeout, and
owner-only access. docs-wiki forwards chat to admin-api `/internal/reader/*`, which is
not under `/api` and is not routed by the admin gateway, authenticating with the
`READER_AGENT_KEY` secret (generated by `make setup`). The agent that answers is the
one chosen in Admin -> Agents -> Reader assistant; it must have grounding on, and
grounded agents get only `search_documents`, `read_document` and `report_not_covered`.

The code chat endpoints below still return HTTP 200 `text/plain` `TODO`. They do not
execute agents or create new conversations.

| Application | POST route |
| --- | --- |
| Administration | `/api/agents/search/chat?agent_id={id}` |
| Administration | `/api/agents/code/ask?agent_id={id}` |
| Reader | `/api/{workspace}/chat/{session_key}/escalate` |
| Code service | `/repos/{name}/ask` |

`knowledge_admin.agents.agent()` runs a configured specialist with the Agents SDK.
The caller provides a saved config, task, history, and decrypted credentials. The
board shares its native `create_agent()` constructor. Responses and Chat Completions
connections use the corresponding SDK model classes; native Anthropic/Bedrock uses
`LitellmModel`. See [the runtime documentation](../services/admin-api/README.md).

Git synchronization, Jira import/export, RAG processing/search and connection tests
are functional integrations. Connection-test buttons may contact their configured
server or model. Keycloak authentication and Phoenix infrastructure remain.

See [agent configuration](agent-profiles.md), [SSH settings](server-operations.md),
[LLM connections](llm-settings.md), and [PostgreSQL migrations](migrations.md).


## Configuration versions

The specialist's **Versions** tab saves full configuration snapshots: prompt text,
additional instructions, model settings, capabilities, resource selections, skills,
MCP definitions and resolved SSH settings. First saving a draft preserves the
current working setup as active version 1. Subsequent snapshots are drafts until
explicit activation. Published snapshots remain available for rollback.

**Add feedback → Save as new version** opens an editor prefilled with proposed
instruction replacements. It copies the configuration reviewed in that session,
not unrelated later edits. Review the prompt, general/server instructions and
full config, label the draft and save. Existing Markdown-only reviews still open
with their original settings and suggestions so replacements can be edited manually.

Use **Test vN** to open a separate browser tab and conversation for that version.
Multiple conversations can run independently. This release uses separate tabs;
a combined side-by-side comparison view is not included. Saved snapshots cannot
be edited in place: **Copy as draft** creates a revised snapshot. Settings tabs
remain the working configuration editor; use **Create draft from settings** to
capture changed models, resources or permissions, then activate that draft.
Coordinator configuration continues to use its existing settings/prompt workflow.

- `GET /api/agents/{id}/versions`: version history and active ID.
- `GET /api/agents/{id}/versions/source`: working config for a new draft; optional
  `version_id` or `review_id` selects an existing snapshot or reviewed session.
- `POST /api/agents/{id}/versions`: source identifiers/hash, label, notes and
  instruction changes. Arbitrary runtime config/credential references are not accepted.
- `POST /api/agents/{id}/versions/{version_id}/activate`: compare-and-set using
  `expected_active_id`. Existing sessions retain their snapshot.
- `GET /api/agents/{id}/config?version_id=...`: selected snapshot. Without a version,
  returns the active snapshot, or working settings until the first snapshot exists.

Migration 0034 adds `ka.agent_versions`, an active pointer/counter on `ka.agents`
and the session's version reference. Older sessions keep their stored configuration.
Snapshots store credential references, never private keys or provider tokens. Credentials
are resolved at execution time; disabled agents/models/SSH/MCP resources still block use.
File contents and external server state are live, not copied into a version. A removed
credential or moved folder can therefore make a historical version unavailable.
