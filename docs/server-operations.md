# SSH configuration

An agent reaches the servers its access object references with each server's own SSH settings.
Administration requires the Keycloak admin group.
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

Server SSH settings support connection tests without creating
an agent run. SSH uses AsyncSSH. Private keys are encrypted at rest with the existing
AES-GCM credential key. Responses expose key metadata, never the private key.
Omitting a replacement key preserves the saved key; explicit removal is separate.

Trusted host keys are optional. When saved they are verified; when absent the
connection test returns the discovered public host key and fingerprint for review.
The server SSH-enabled flag and sudo permission remain configuration controls.

The `/api/servers/{id}/ssh` endpoints manage a server's SSH settings; every agent uses them (per-agent connection
overrides are gone).
