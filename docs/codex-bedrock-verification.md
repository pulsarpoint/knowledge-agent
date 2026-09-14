# Historical Codex SDK and Bedrock adapter verification

This records the earlier runtime. The Agents SDK migration replaces it with direct
provider connections; see `services/admin-api/README.md` for the current setup.

The current `knowledge_admin.agents.agent()` uses the official Python Codex SDK.
For the saved native Claude/Bedrock model, `model_adapter.responses_adapter()`
starts a temporary authenticated loopback endpoint and uses LiteLLM to translate
Codex's Responses requests into Bedrock Invoke requests. It keeps the saved model,
region and credential. Native credentials never enter the prompt or SDK process;
Codex authenticates to the adapter using a temporary token.

Live verification on 2026-09-11 used the saved `eu.anthropic.claude-opus-5`
connection in `eu-central-1`. The SDK received `ADAPTER_OK`, then completed a
separate task reading an isolated marker file through its command tool. The marker
was absent from the prompt, proving the tool result returned to Claude. No SSH
server was contacted and no saved configuration was changed.

The deployed admin API also completed a real SDK/Bedrock command-tool round trip
inside Docker, reading `/work/code/Hello-World (public test)/README` and returning
its exact contents. The command sandbox grants read access to the SDK's bundled
runtime package as well as the selected source folders. A regression test checks
that the SDK command tool can execute successfully under this configuration.

Automated tests exercise the real SDK and LiteLLM Chat Completions conversion,
streamed tool argument fragments, MCP execution and the matching tool result in
the next provider request. They also cover provider routing, authentication,
concurrent run isolation, errors and cancellation. Execution HTTP endpoints remain
`TODO`; this verification calls the backend function directly.

See [the entry point and supported adapter API](../services/admin-api/README.md#running-a-configured-agent-with-the-codex-sdk)
and [execution endpoint status](agents.md).
