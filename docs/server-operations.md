# SSH configuration

Every agent has an SSH tab and can store selected servers, per-server instructions,
and optional connection overrides. Administration requires the Keycloak admin group.
Agent execution currently returns `TODO`; saving configuration does not connect to a server.

## Setup

1. In **Servers**, register a host and enable **SSH allowed**. Open **SSH settings** to configure its default login user, private key, port, trusted host keys, and sudo permission.
2. In an agent’s **General** tab, choose a model and configure its instructions. Its active system prompt is managed in **Prompts**.
3. In **SSH**, select one or more allowed servers and save. You can configure servers while the agent’s SSH access is off; enable and save SSH access when the agent should be able to connect. **Configure** beside each server opens its instructions, SSH connection, skills, and MCP configuration. Servers can be selected before credentials are ready; connection tests report missing or invalid credentials.
4. **Configure SSH connection** shows the resolved connection. By default it uses the server inventory configuration. Turn on **Override server SSH configuration** to save a connection for this agent only. It starts with the current server settings and saved key. You can change its user, key, port, trust entries, and sudo permission. Later changes to the server defaults do not replace an override. Turning the switch off shows a confirmation dialog and restores the current server configuration.
5. **Create server configuration** adds optional instructions and skill/MCP selections for this agent on this server. Uncheck **Use agent instructions** to enter a server-specific procedure. Empty instructions explicitly mean no additional instructions. Removing this configuration restores the agent’s instructions and selections, and keeps SSH authentication and allowed-server selection intact.
6. In **Test**, run a task and inspect the answer and command history. The agent receives server-specific instructions under the matching server ID, along with its effective login and sudo policy. It cannot choose a different login or supply a private key itself.

The server inventory’s **SSH allowed** flag applies even when an agent has an override. The effective connection’s sudo permission controls root execution. No override grants access by itself: the server must also be selected in that agent’s allowed-server list.

## Connection tests and credentials

Server defaults and per-agent overrides support connection tests without creating
an agent run. SSH uses AsyncSSH. Private keys are encrypted at rest with the existing
AES-GCM credential key. Responses expose key metadata, never the private key.
Omitting a replacement key preserves the saved key; explicit removal is separate.

Trusted host keys are optional. When saved they are verified; when absent the
connection test returns the discovered public host key and fingerprint for review.
The server SSH-enabled flag and sudo permission remain configuration controls.
Changing a server default does not replace an agent's explicit connection override.

## Agent SSH API

- `GET /api/agents/{agent_id}/servers/{server_id}/ssh` returns the effective connection, its `connection_source` (`server` or `agent`), override `revision` (zero when absent), and `server_revision`.
- `PUT` at the same path saves an override. Send both revisions plus connection fields. An omitted private key preserves the effective saved key; explicit `null` removes authentication for this override.
- `POST .../ssh/test` previews a connection with the same revisions, without saving credentials or creating an override.
- `DELETE .../ssh` requires both revisions and `confirmed: true`, removes the override, and returns the server configuration. Instructions and server selection are preserved.

The original `/api/servers/{id}/ssh` endpoints manage server defaults. `GET /api/operations/settings?agent_id={id}` lists effective SSH configurations for that agent.
