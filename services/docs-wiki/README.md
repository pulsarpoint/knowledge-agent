# Reader API

FastAPI serves document browsing/search and the React reader. Existing chat and
conversation history URLs remain. Chat (and legacy escalation) returns plain text
`TODO`; it does not call an agent, model, or tool or create a conversation.

User-scoped historical conversations remain readable/deletable. The retained
search tools live in `src/docs_wiki/tools/`; they are disconnected from chat.
Keycloak protects the deployed application. Phoenix configuration is unchanged.

See [repository README](../../README.md) and [execution status](../../docs/agents.md).
Run `uv run --frozen pytest -q` for reader tests.
