# SSH configuration

An agent reaches the servers its access object references with each server's own SSH settings.
Administration requires the Keycloak admin group.
Agent execution currently returns `TODO`; saving configuration does not connect to a server.

## Setup

1. In **Servers**, register a host and enable **SSH allowed**. Open **SSH settings** to configure its default login user, private key, port, trusted host keys, and sudo permission.
2. In an agent’s **General** tab, choose a model and configure its instructions. Its active system prompt is managed in **Prompts**.
3. In the agent's **Resources**, add one or more allowed servers and save. Servers can be added before credentials are ready; connection tests report missing or invalid credentials.
4. The agent connects with the server's inventory configuration: its login user, private key, port, trusted host keys and sudo permission. Change them under **Servers → SSH settings**.
5. In **Test**, run a task and inspect the answer and command history. The agent receives its effective login and sudo policy. It cannot choose a different login or supply a private key itself.

The server inventory’s **SSH allowed** flag always applies. The connection’s sudo permission controls root execution. Nothing grants access by itself: the server must also be in the agent’s Resources.

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
