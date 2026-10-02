# Reader chat answers with a generic admin-configured agent

Status: design approved 2026-10-02; awaiting spec review.

## Goal

Signed-in employees ask questions in the Reader (`/:app/chat`) and receive answers
grounded only in the RAG knowledge base, with clickable `[n]` citations. When the
documentation does not cover a question, the answer says so and the question is
recorded as a documentation gap. Conversations appear in Admin → Activity.

Success criteria:

- A question covered by the docs returns a streamed answer whose `[n]` markers link
  to the cited documents.
- A question not covered by the docs shows the "not covered" badge, adds an entry to
  Reader → Gaps, and never invents an answer from general knowledge.
- Every completed turn appears in Admin → Activity.
- No new agent runtime exists outside admin-api; the Reader runs an agent defined and
  versioned in Admin → Agents.

## Agreed decisions

| Decision | Choice |
| --- | --- |
| Grounding | Docs only. Every claim is cited; uncovered questions are stated as such. |
| Gap signal | The agent calls `report_not_covered`, or at least one search ran and every search returned zero hits. |
| Runtime | The generic runner `knowledge_admin.agents.agent()` in admin-api. docs-wiki is an adapter only. |
| Agent selection | A *Reader assistant* setting in admin chooses the agent; default is the existing `search` agent. |
| Access | Reader access tiers. Step 1 ships the **Knowledge** tier only. |

### Reader access tiers

| Tier | Reader users | Allowed agent capabilities | Delivered |
| --- | --- | --- | --- |
| Knowledge | Any signed-in user | Documents, Skills, Services | This spec |
| Support | Members of an admin-chosen Keycloak group | Knowledge + read-only SSH, MCP, Code | Step 1b, separate spec |

Reason: `ssh_run` currently executes arbitrary commands (optionally with sudo) and the
Reader has no group restriction, so SSH-capable Reader agents would give every
signed-in user the agent's server access. Step 1b introduces a read-only SSH mode,
group gating and per-user command audit before SSH reaches the Reader.

## Architecture

```
Reader UI ─► docs-wiki POST /api/{app}/chat ──(internal network, X-Reader-Key)──► admin-api POST /internal/reader/runs
              │ user identity, sessions,                           │ load reader settings + agent's active version
              │ audit, gaps, event mapping                         │ enforce Knowledge tier, intersect folders
              ◄──────────── NDJSON events ─────────────────────────┘ agents.agent(config, question, history, grounded=True)
```

### Units

1. **`ka.reader_settings`** (migration `0060_reader_settings`): single-row table
   `{singleton, knowledge_agent_id → ka.agents(id), revision, updated_by, updated_at}`,
   seeded with `search`. The down migration drops it. Deleting an agent that is the
   Reader agent is refused by the foreign key (`ON DELETE RESTRICT`) and surfaced as
   "This agent is the Reader assistant. Choose another Reader agent first."

2. **Reader settings API and UI** (admin-api + admin-ui): `GET/PUT /api/reader/settings`
   with revision checks and audit events. A *Reader assistant* card on `/agents`
   shows the selected agent and a select of agents that pass the tier rule; agents
   that fail show the reason ("uses SSH", …) and are disabled in the select.

3. **Tier policy** (`knowledge_admin/reader_policy.py`):
   `knowledge_violations(config) -> list[str]` returns human-readable reasons when the
   effective configuration enables SSH, MCP, or Code. Empty list = allowed. Used on
   settings save and on every run.

4. **`search_documents` tool** (`knowledge_admin/tools/documents.py`): added by
   `get_tools` whenever the Documents capability is on (all agents, not only the
   Reader). Arguments `query`, optional `folder` (must lie inside the allowed
   folders). Calls the RAG `/search` API (hybrid, top 8) with the run's folder
   selectors, using `RAG_BACKOFFICE_KEY`. Each hit gets a turn-wide number `n` and is
   recorded in the run context. Output: `[n] path (folder)\n<chunk text>` blocks, or
   "No matches in the documentation." RAG failures return an error string to the model.
   A companion **`read_document(path)`** returns one RAG workspace file (≤ 64 KiB) when
   its folder is allowed; its content is registered as a citable excerpt.

5. **Grounded run option** (`agents.agent(..., grounded=False)`): when true,
   - appends the grounding policy to the instructions (answer only from retrieved
     excerpts; cite claims with `[n]`; when the excerpts do not answer the question
     call `report_not_covered(reason)` and tell the user what is missing; never use
     general knowledge for factual claims; do not cite numbers that were not returned);
   - adds the `report_not_covered(reason: str)` tool;
   - requires the Documents capability (else `ValueError`).
   The run result exposes `grounding = {excerpts, searches, empty_searches, reported}`
   through the run context so callers can build sources and decide `not_covered`.

6. **Internal endpoint** (`knowledge_admin/reader_api.py`): `POST /internal/reader/runs`,
   mounted outside `/api` so the admin gateway and nginx (`location /api/`) never
   route to it. Authenticated by constant-time comparison of `X-Reader-Key` with
   `READER_AGENT_KEY`; missing configuration → 503, wrong key → 401.
   Request: `{question (1–10000), history (list, ≤ 40 items), folders: list[str] | null,
   include_subfolders: bool, user_email}`.
   Steps: load reader settings → load the agent's effective configuration with the
   existing `agents.get_agent_config(agent_id)` (active configuration version if
   present, otherwise working configuration; never `working_copy=True`) → tier check
   → intersect document folders with the request's folders → resolve credentials →
   `agents.agent(config, question, history=history, grounded=True, max_turns=20,
   timeout_seconds=300)` streamed.
   Response: `application/x-ndjson` with events
   `tool {name, argument}`, `token {text}`, `final {text, sources, not_covered, near_misses, results, items}`,
   `error {message}`. `sources` contains only excerpts whose `[n]` appears in the final
   text, as `{n, path, heading_path, anchor: null}`. `items` is the SDK
   `to_input_list()` for docs-wiki to persist. The email is used only for tracing
   metadata.

7. **Test tab toggle** (admin-ui): "Grounded (Reader mode)" switch on the agent Test
   tab passes `grounded=true` to the existing runs endpoint, so admins can preview
   Reader behavior. Sources are shown under the reply.

8. **docs-wiki adapter** (`search_api.py` `/chat`):
   1. Resolve the user (existing `user_state`), validate the question, load or create
      the session (`session` event for new keys), bind context (existing
      `bind_context`).
   2. Refuse a second concurrent turn in the same session with 409
      "Wait for the current answer." (per-session asyncio lock in process).
   3. Read history via `CappedSession.get_items()`, POST to
      `READER_AGENT_URL/internal/reader/runs` with `X-Reader-Key`, stream the response.
   4. Map events: `tool` → `tool`, `token` → `token`, `final` → `sources` then
      `done {text, not_covered, escalation_offered: false, sources_used: ["docs"]}`,
      `error` → `error`.
   5. After `final`: persist `items` to the session, append an audit row
      (`audit.append`), and when `not_covered` append a gap (`gaps.append_gap` with
      the near misses, `results`, and the existing session-ref hash).
   6. On error or disconnect: persist nothing.
   `/chat/{key}/escalate` keeps returning `TODO`; the escalation button stays hidden.

9. **Reader UI** (`services/docs-wiki-ui/app/lib/api.ts`, `packages/chat-ui`):
   `streamChat()` parses NDJSON (buffering partial lines; malformed lines become an
   `error` event) into the existing `ChatEvent` union. Remove the "Agent execution is
   not implemented yet" footer. Hide the repository portion of Advanced context.

## Error handling

| Condition | HTTP / event | Message to the user |
| --- | --- | --- |
| No reader agent, agent disabled, or no enabled model | 409 before streaming | "The assistant isn't configured yet. Ask an administrator." |
| Tier violation at run time | 409 before streaming | "The assistant's configuration isn't allowed in the Reader." |
| admin-api unreachable or 5xx | 503 / `error` | "Chat is temporarily unavailable." |
| Model, timeout, turn limit, provider error | `error` | Existing redacted, generic messages from the agent runtime |
| Concurrent turn in a session | 409 | "Wait for the current answer." |
| Invalid key between services | 401 (logged in docs-wiki) | "Chat is temporarily unavailable." |

Secrets are never forwarded: docs-wiki sends no gateway headers upstream, and the
agent runtime's existing redaction applies to tool output, answers and traces.

## Configuration

- `scripts/setup.py` generates `READER_AGENT_KEY` (32 random bytes, base64), added to
  `.env.example`.
- `deploy/compose.yaml`: docs-wiki gets `READER_AGENT_URL=http://admin-api:8766` and
  `READER_AGENT_KEY`; admin-api gets `READER_AGENT_KEY`. docs-wiki does **not** add a
  `depends_on` on admin-api, so reading and search start and keep working while
  admin-api is down; the adapter reports chat unavailability per request.

## Testing

All new behavior is test-first. No test calls a real model; admin-api tests reuse the
fake model approach in `tests/test_sdk_agent.py`.

- admin-api: `search_documents`/`read_document` scoping, numbering and RAG-error
  handling; grounded policy text and tool injection; cited-only sources; empty-search
  `not_covered`; tier policy on save and run; internal endpoint key handling and
  absence from `/api`; active-version selection; reader settings revision checks and
  FK refusal on agent delete.
- docs-wiki: event mapping, session persistence only on success, audit and gap rows,
  every error row in the table above, concurrent-turn refusal.
- UI: NDJSON parser (split lines, malformed line), footer removal, Reader assistant
  card, Test tab grounded toggle.
- DB: `0060` up/down in `db/tests`.
- `scripts/smoke.py`: internal endpoint rejects a request without the key. Smoke stays
  model-free.

## Documentation

Update `docs/user-guide.md` (Reader chat, Gaps, Activity, Agents, limitations,
Appendix B) and the README sentence stating that chat returns `TODO`.

## Out of scope

- Step 1b: read-only SSH mode, Support tier, group gating, per-user command audit.
- "Ask the code" escalation.
- Publishing generated documents to RAG automatically.
- Per-folder reader permissions.
- Moving Reader sessions, audit or gaps from SQLite to PostgreSQL.
