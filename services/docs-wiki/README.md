# Reader API

FastAPI serves document browsing/search and the React reader. In reader mode
(`READER_AGENT_URL` and `READER_AGENT_KEY` set), chat is an adapter: `POST /api/{app}/chat` and the
`/api/{app}/chats` history routes forward to admin-api `/internal/reader/*` with the
`X-Reader-Key` secret and the signed-in user's identity, and map admin-api's events
to the Reader's SSE protocol (`session`, `tool`, `sources`, `done`, `error`, plus
`: heartbeat` comments). The agent that answers is the one chosen in Admin -> Agents
-> Reader assistant. Conversations live in admin-api (owner-only, 7-day idle
timeout); docs-wiki stores none. Questions the documents do not cover are recorded
locally as gaps before the answer is sent. Keys that are not admin-api conversation
IDs (such as old file-based conversations) are reported as `Unknown conversation`.
Legacy escalation still returns plain text `TODO`.

The retained search tools live in `src/docs_wiki/tools/`.
Keycloak protects the deployed application. Phoenix configuration is unchanged.

See [repository README](../../README.md) and [execution status](../../docs/agents.md).
Run `uv run --frozen pytest -q` for reader tests.
