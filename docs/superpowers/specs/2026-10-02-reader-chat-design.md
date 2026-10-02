# Reader chat answers with a generic admin-configured agent

Status: design approved 2026-10-02 (revised: grounding as agent configuration, sessions
in `ka.agent_sessions`); awaiting spec review.

## Goal

Signed-in employees ask questions in the Reader (`/:app/chat`) and receive answers
grounded only in the RAG knowledge base, with clickable `[n]` citations. When the
documentation does not cover a question, the answer says so and the question is
recorded as a documentation gap. Administrators can review real Reader conversations
and tune the agent from them.

Success criteria:

- A question covered by the docs returns an answer whose `[n]` markers link to the
  cited documents.
- A question not covered by the docs shows the "not covered" badge, adds an entry to
  Reader → Gaps, and never invents an answer from general knowledge.
- Every Reader conversation is a live session (`is_test = false`) in
  `ka.agent_sessions`, visible in Admin → Activity, and reviewable with
  **Suggest improvements**.
- No agent runtime or session store is added outside admin-api; the Reader runs an
  agent defined and versioned in Admin → Agents. `agents.agent()` and
  `create_agent()` keep their signatures.

## Agreed decisions

| Decision | Choice |
| --- | --- |
| Grounding | Docs only. Every claim is cited; uncovered questions are stated as such. |
| Grounding switch | Agent configuration: **Answer only from documents** on the Documents capability (`documents.grounded`), versioned like every other setting. Not a run option. |
| Gap signal | The agent calls `report_not_covered`, or at least one search ran and every search returned zero hits. |
| Runtime | The generic runner `knowledge_admin.agents.agent()` in admin-api. |
| Sessions | The generic `ka.agent_sessions` / `ka.operations_runs` system, live sessions (`is_test = false`). docs-wiki stores no conversations. |
| Agent selection | A *Reader assistant* setting in admin chooses the agent; default is the existing `search` agent. |
| Access | Reader access tiers. Step 1 ships the **Knowledge** tier only. |

### Reader access tiers

| Tier | Reader users | Allowed agent capabilities | Delivered |
| --- | --- | --- | --- |
| Knowledge | Any signed-in user | Documents (with **Answer only from documents** on), Skills, Services | This spec |
| Support | Members of an admin-chosen Keycloak group | Knowledge + read-only SSH, MCP, Code | Step 1b, separate spec |

Reason: `ssh_run` currently executes arbitrary commands (optionally with sudo) and the
Reader has no group restriction, so SSH-capable Reader agents would give every
signed-in user the agent's server access. Step 1b introduces a read-only SSH mode,
group gating and per-user command audit before SSH reaches the Reader.

## Architecture

```
Reader UI ─► docs-wiki /api/{app}/chat|chats ──(internal network, X-Reader-Key, X-Reader-User)──► admin-api /internal/reader/*
              │ identity from gateway,                          │ reader settings + tier check + folder narrowing
              │ event mapping, gap logging                      │ shared session/turn service (same code as Test)
              ◄──────── NDJSON events ──────────────────────────┘ agents.agent(config, question, history=session history)
                                                                  ka.agent_sessions (is_test=false) · ka.operations_runs
```

## Units

### 1. Migration `0060_reader_assistant`

- `ka.agents.documents_grounded boolean NOT NULL DEFAULT false`; set `true` for `search`.
- `ka.agent_sessions.idle_timeout interval NOT NULL DEFAULT interval '1 hour'` — the
  idle window before an active session expires. Existing sessions keep one hour.
- `ka.agent_sessions.hidden_by_owner_at timestamptz NULL` — set when a Reader user
  deletes a conversation from their list; the transcript stays available to admins.
- `ka.reader_settings`: single row
  `{singleton, knowledge_agent_id → ka.agents(id) ON DELETE RESTRICT, revision,
  updated_by, updated_at}`, seeded with `search`.
- The down migration drops the table and the three columns.

Deleting an agent that is the Reader agent fails with "This agent is the Reader
assistant. Choose another Reader agent first."

### 2. "Answer only from documents" setting

A switch on the agent's **Documents** tab (shown while Documents is enabled), stored
as `documents_grounded` and exposed as `config['documents']['grounded']`. It is part of
the working configuration and version snapshots, so drafts, activation and rollback
apply. When on:

- `get_prompt(config)` appends the grounding policy: answer only from retrieved
  excerpts; cite claims with `[n]`; when the excerpts do not answer the question call
  `report_not_covered(reason)` and tell the user what is missing; never use general
  knowledge for factual claims; never cite numbers that were not returned.
- `get_tools(config)` adds `report_not_covered(reason: str)`.

Any agent may enable it; the Reader requires it.

### 3. Document tools (`knowledge_admin/tools/documents.py`)

Added by `get_tools` whenever the Documents capability is on, for every agent:

- `search_documents(query, folder?)` — RAG hybrid search (top 8) restricted to the
  run's allowed folders (an optional `folder` must lie inside them), using
  `RAG_BACKOFFICE_KEY`. Each hit receives a turn-wide number `n`. Output:
  `[n] path (folder)\n<chunk text>` blocks, or "No matches in the documentation."
  RAG failures return an error string to the model.
- `read_document(path)` — one RAG workspace file (≤ 64 KiB) when its folder is
  allowed; registered as a citable excerpt.

Both record `grounding = {excerpts, searches, empty_searches, reported}` in the
run-context dict that `agent()` already creates. Callers read it after the run via the
SDK's `run.context_wrapper.context`. `report_not_covered` sets `reported`.

### 4. Shared turn service (refactor of `agent_sessions.py`)

Extract the body of `POST /api/agents/{id}/runs` into
`start_turn(store, app_name, *, agent_id, value, actor, config_override=None,
idle_timeout=None, owner_only=False) -> AsyncIterator[event]`, used unchanged by the
admin route (behavior and SSE format unchanged) and by the Reader endpoint. Additions,
all generic:

- **Grounding result:** on completion, when `grounding` exists in the run context,
  store `context['grounding'] = {sources, not_covered, near_misses, results}` on the
  run row, where `sources` are the excerpts whose `[n]` appears in the answer
  (`{n, path, heading_path, anchor: null}`) and `not_covered` follows the gap signal.
  The `completed` event therefore carries it.
- **Idle timeout:** expiry SQL uses `s.idle_timeout` instead of the literal one hour;
  `expires_at` likewise. `create_session` takes an optional `idle_timeout`.
- **Owner check (`owner_only`):** a follow-up turn on an existing session requires
  `actor_id` to be the session owner and `hidden_by_owner_at IS NULL`; otherwise 404.
- **Config override:** for a new session, the caller may pass the already-resolved
  configuration to pin (used by the Reader for folder narrowing).

### 5. Tier policy (`knowledge_admin/reader_policy.py`)

`knowledge_violations(config) -> list[str]` returns readable reasons when SSH, MCP or
Code is enabled, Documents is off, or **Answer only from documents** is off. Empty
list = allowed. Checked when saving reader settings and before every Reader turn
(against the pinned session configuration).

### 6. Reader settings API and UI

`GET/PUT /api/reader/settings` with revision checks and audit events. A *Reader
assistant* card on `/agents` shows the selected agent and a select of agents; agents
that violate the tier rule are disabled with their reason ("uses SSH", "Answer only
from documents is off", …).

### 7. Internal Reader endpoints (`knowledge_admin/reader_api.py`)

Mounted under `/internal/reader`, outside `/api`, so the admin gateway and nginx
(`location /api/`) never route to them. Authentication: constant-time comparison of
`X-Reader-Key` with `READER_AGENT_KEY` (unset → 503, wrong → 401). The Reader user is
`X-Reader-User` (subject) and `X-Reader-Email`, trusted only because the key matched;
it becomes the session and run actor through the existing principals table.

| Route | Behavior |
| --- | --- |
| `POST /internal/reader/runs` | `{id?, session_id?, question (1–10000), folders: list[str] \| null, include_subfolders}`. New session: load reader settings → `get_agent_config(agent_id)` (active version, never working copy) → tier check → narrow document folders to the intersection with `folders` → `start_turn(..., config_override, idle_timeout=interval '7 days', owner_only=True)` with `is_test=false`, `max_turns` 20, timeout 300 s. Existing session: owner check, tier check on the pinned config, `start_turn`. Streams `application/x-ndjson`: `session {id}` (new sessions), `activity {title, tool, argument}`, `completed {status, answer, error, grounding}`. |
| `GET /internal/reader/sessions` | The user's visible live sessions for the Reader agent, newest first, max 50: `{id, title, status, last_activity_at}`. |
| `GET /internal/reader/sessions/{id}` | Owner-only transcript: user/assistant messages from the session's runs, each assistant message with `sources` and `not_covered` from its run's grounding, plus pinned `folders`. |
| `DELETE /internal/reader/sessions/{id}` | Owner-only: close the session and set `hidden_by_owner_at`. Transcript stays visible to admins. |

### 8. docs-wiki adapter (`search_api.py`)

docs-wiki keeps no conversation state. `sessions.py`, `audit.py` writers and the
`search-conversations` state are no longer used by the workspace-mode routes.

| Reader route | Adapter behavior |
| --- | --- |
| `POST /api/{app}/chat` | Require the gateway identity (existing `user_state`), validate, call `POST /internal/reader/runs`, map events: `session` → `session {key}`, `activity` → `tool {name, argument}`, `completed` (status completed) → `sources` then `done {text, not_covered, escalation_offered: false, sources_used: ["docs"]}`, otherwise → `error {message}`. When `not_covered`, append a gap (`gaps.append_gap` with near misses, `results`, and a session-ref hash). |
| `GET /api/{app}/chats` | Proxy to `GET /internal/reader/sessions`, mapped to the existing `{session_key, first_question, …}` shape. |
| `GET /api/{app}/chats/{key}` | Proxy to the transcript; map to the existing `Transcript` type. |
| `DELETE /api/{app}/chats/{key}` | Proxy to delete. |
| `POST /api/{app}/chat/{key}/escalate` | Unchanged: `TODO`; the escalation button stays hidden. |

Gateway headers are never forwarded; docs-wiki sends only `X-Reader-Key`,
`X-Reader-User` and `X-Reader-Email`.

### 9. Reader UI (`services/docs-wiki-ui/app/lib/api.ts`, `packages/chat-ui`)

- `streamChat()` parses NDJSON (buffers partial lines; a malformed line becomes an
  `error` event) into the existing `ChatEvent` union.
- The answer appears when the turn completes; activity lines ("searching: …",
  "reading: …") show progress meanwhile. Token-by-token streaming is not provided,
  because the generic runner reports activity, not text deltas.
- Session keys are the session UUIDs.
- Remove the "Agent execution is not implemented yet" footer; hide the repository
  part of Advanced context; change the delete dialog text to "Remove this
  conversation from your list?"
- Continuing an expired conversation shows "This conversation has ended. Start a new
  one." with a **New conversation** link.

### 10. Admin: Activity and Test

- **Activity** (`/activity`, user and session pages) reads live sessions
  (`is_test = false`) for the Reader agent from `ka.agent_sessions` /
  `ka.operations_runs` instead of the Reader's `audit.db`: users, their sessions, and
  each question with answer, sources and the not-covered badge. A session page links to
  the agent's session view, where **Suggest improvements** works on real
  conversations. Historical `audit.db` rows are not migrated (nothing has written them
  in v3).
- **Test tab:** no new toggle. Replies whose run carries `grounding.sources` list
  their sources under the reply.

## Error handling

| Condition | HTTP / event | Message to the Reader user |
| --- | --- | --- |
| No Reader agent, agent disabled, or no enabled model | 409 before streaming | "The assistant isn't configured yet. Ask an administrator." |
| Tier violation | 409 before streaming | "The assistant's configuration isn't allowed in the Reader." |
| Session not owned, hidden, or unknown | 404 | "Unknown conversation" |
| Session expired or closed | 409 | "This conversation has ended. Start a new one." |
| Turn already running in the session | 409 | "Wait for the current answer." |
| admin-api unreachable, 5xx, or key rejected | 503 / `error` | "Chat is temporarily unavailable." (key rejection is logged in docs-wiki) |
| Model, timeout, turn limit, provider error | `error` | The turn service's existing redacted messages |

Reading and search never depend on admin-api.

## Configuration

- `scripts/setup.py` generates `READER_AGENT_KEY` (32 random bytes, base64); add it to
  `.env.example`.
- `deploy/compose.yaml`: docs-wiki gets `READER_AGENT_URL=http://admin-api:8766` and
  `READER_AGENT_KEY`; admin-api gets `READER_AGENT_KEY`. docs-wiki does not add a
  `depends_on` on admin-api.
- Reader session idle timeout: 7 days (constant in `reader_api.py`).

## Testing

All new behavior is test-first. No test calls a real model; admin-api tests reuse the
fake-model approach in `tests/test_sdk_agent.py`.

- admin-api
  - document tools: folder scoping (agent ∩ request), numbering, RAG errors,
    `read_document` limits;
  - grounding setting: policy and `report_not_covered` only when on; saved, versioned,
    shown on the Documents tab; `agent()` / `create_agent()` signatures unchanged;
  - turn service: admin `/runs` behavior and SSE unchanged (existing tests stay
    green); grounding result with cited-only sources and empty-search `not_covered`;
    `idle_timeout` expiry; owner check;
  - tier policy on settings save and per turn; reader settings revision checks;
    FK refusal on agent delete;
  - internal endpoints: key handling, unreachable through `/api`, owner isolation
    between two users, hide-on-delete keeps the admin view, active version pinned;
  - Activity endpoints read live sessions.
- docs-wiki: event mapping for every event and error row; gap appended only when
  `not_covered`; list/transcript/delete mapping; no gateway headers forwarded.
- UI: NDJSON parser (split lines, malformed line); expired-conversation message;
  footer and repository picker removed; Reader assistant card; Documents-tab switch;
  Test-tab and Activity source lists.
- DB: `0060_reader_assistant` up/down in `db/tests`.
- `scripts/smoke.py`: internal endpoints reject a request without the key. Smoke stays
  model-free.

## Documentation

Update `docs/user-guide.md` (Reader chat, Gaps, Activity, Agents, limitations,
Appendix B), `docs/agents.md` (Reader is connected; live sessions; idle timeout), and
the README sentence stating that chat returns `TODO`.

## Out of scope

- Step 1b: read-only SSH mode, Support tier, group gating, per-user command audit.
- Token-by-token answer streaming.
- "Ask the code" escalation.
- Publishing generated documents to RAG automatically.
- Per-folder reader permissions.
- Moving the Gaps log from `gaps.jsonl` to PostgreSQL.
