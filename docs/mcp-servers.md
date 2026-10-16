# MCP servers

Agent execution currently returns `TODO`. Connection settings, encrypted credentials,
and connection tests remain functional; runtime behavior described below is retained
as design context only. See [execution status](agents.md).

**Administration → MCP servers** manages Streamable HTTP connections for Code search and SSH. Connections have a name, description, endpoint, optional bearer token, enabled state, and a discovered tool catalog.

1. Add the MCP endpoint, for example `https://tools.example.com/mcp`. The address must be reachable from Docker. Use `host.docker.internal` for services running on the Docker Desktop host; `localhost` refers to the container. Code-agent and admin-api share the configured `DOCKER_DNS_SEARCH` suffix.
2. Save and click **Test connection**. This initializes an MCP session and lists all tools, including paginated catalogs. It does not execute tools. Expand the tool count to inspect names and descriptions.
3. Select the connection in **Agents → Code search → Settings → Additional tools**, or **Agents → SSH → Settings → MCP servers**, and save. Only enabled, successfully tested connections with tools can be selected.
4. Use the agent's **Test** tab to run a task that needs the selected tools.

Code selection applies to direct code tests and code investigations invoked by the Search agent. Code prompts require investigation without remote changes; each MCP service's credentials and own authorization determine the operations its tools can actually perform. Choose connection permissions appropriate to the agent.

SSH MCP selections follow the existing Global/server configuration. A server without an override inherits Global. In an override, **Use global MCP servers** keeps inheritance; turning it off allows a replacement selection, including an empty list. Removing the server configuration restores inheritance. A multi-server run receives the union of its resolved MCP selections, with per-server selections in its context. MCP connections authorize tools on their own external systems; they are not an additional SSH server allowlist. Built-in SSH tools retain their server and sudo checks.

## Credentials and execution

Bearer tokens are encrypted with AES-256-GCM using `CREDENTIAL_ENCRYPTION_KEY`, a fresh 12-byte nonce, and connection-specific authenticated data. API responses expose only `has_token`. Leaving the replacement field blank preserves the saved token; removal is explicit. Changing an endpoint with a saved token requires replacing or removing that token, and changing endpoint/authentication clears the test catalog.

The shared Codex runtime connects to an internal HTTP relay using a temporary run token. Actual MCP bearer tokens remain in the application service. The relay forwards MCP session headers, JSON or SSE responses, and only calls from the tested tool catalog. Provider requests cannot add access to other MCP connections or restore disabled local tools on the SSH agent. MCP settings and cancellation are checked before forwarding requests. Disabling, editing, or deleting a connection revokes subsequent calls from existing runs; already completed external changes cannot be undone by revocation.

The first implementation supports HTTP tool servers and optional static bearer tokens. OAuth, local stdio commands, MCP resources, and prompts are not part of this integration.

## Administration API

All endpoints require the existing Keycloak administrator group.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/mcp/servers` | List connections and discovered tools, without credentials |
| `POST /api/mcp/servers` | Create a connection |
| `PUT /api/mcp/servers/{id}` | Update with `revision`; omit `token` to preserve it, use `null` to remove it |
| `POST /api/mcp/servers/{id}/test` | Initialize and discover tools using the supplied `revision` |
| `DELETE /api/mcp/servers/{id}` | Remove the connection with `revision` and `confirmed: true` (agents referencing it show it as missing) |

An agent uses an MCP server when its access object references it (`{"type": "mcp_server", "id": ...}`). Migration `0019_mcp_servers.up.sql` added the connection registry; the per-agent selections it added were replaced by the access object (0113, 0114).
