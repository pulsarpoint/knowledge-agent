import type { MCPServer } from "~/features/llm/mcp";
import { request } from "~/lib/api";
export interface SSHServer {
  id: string;
  name: string;
  hostname: string;
  port: number;
  username: string | null;
  revision: number;
  server_revision?: number;
  connection_source?: "server" | "agent";
  ssh_allowed: boolean;
  enabled: boolean;
  allow_sudo: boolean;
  known_hosts: string;
  has_private_key: boolean;
  last_tested_at: string | null;
  last_test_error: string | null;
}
export interface SSHInput {
  revision: number;
  server_revision?: number;
  username: string | null;
  port: number;
  allow_sudo: boolean;
  known_hosts: string;
  private_key?: string | null;
}
export interface SSHTestResult {
  ok: boolean;
  message: string;
  host_key: {
    public_key: string;
    fingerprint: string;
    algorithm: string;
    known_hosts_line: string;
    verified: boolean;
  } | null;
}
export interface OperationsSettings {
  revision: number;
  instructions: string;
  server_ids: string[];
  skill_ids: string[];
  mcp_server_ids: string[];
}
export interface OperationsServerConfig {
  id: string;
  server_id: string | null;
  revision: number;
  instructions: string | null;
  skill_ids: string[] | null;
  mcp_server_ids: string[] | null;
}
export interface ServerConfigInput {
  settings_revision: number;
  revision: number | null;
  instructions: string | null;
  skill_ids: string[] | null;
  mcp_server_ids: string[] | null;
}
export interface OperationsConfiguration {
  settings: OperationsSettings;
  mcp_servers: MCPServer[];
  configs: OperationsServerConfig[];
  servers: SSHServer[];
  skills: { id: string; name: string; description: string; enabled: boolean }[];
}
const json = (method: string, value?: unknown) => ({
  method,
  headers: { "Content-Type": "application/json" },
  ...(value === undefined ? {} : { body: JSON.stringify(value) }),
});
const sshPath = (id: string, agentId?: string) =>
  agentId ? `/api/agents/${encodeURIComponent(agentId)}/servers/${id}/ssh` : `/api/servers/${id}/ssh`;
export const getSSH = (id: string, agentId?: string) => request<SSHServer>(sshPath(id, agentId));
export const saveSSH = (id: string, value: SSHInput, agentId?: string) =>
  request<SSHServer>(sshPath(id, agentId), json("PUT", value));
export const testSSH = (id: string, value: SSHInput, agentId?: string) =>
  request<SSHTestResult>(sshPath(id, agentId) + "/test", json("POST", value));
export const getOperationsSettings = (agentId = "operations") =>
  request<OperationsConfiguration>(scope("/api/operations/settings", agentId));
export const saveOperationsSettings = (s: OperationsSettings, agentId = "operations") =>
  request<OperationsSettings>(
    scope("/api/operations/settings", agentId),
    json("PUT", {
      revision: s.revision,
      instructions: s.instructions,
      server_ids: s.server_ids,
      skill_ids: s.skill_ids,
      mcp_server_ids: s.mcp_server_ids,
    }),
  );

export const saveOperationsServerConfig = (serverId: string, config: ServerConfigInput, agentId = "operations") =>
  request<OperationsServerConfig>(scope(`/api/operations/configs/${serverId}`, agentId), json("PUT", config));
export const deleteOperationsServerConfig = (
  serverId: string,
  revision: number,
  settingsRevision: number,
  agentId = "operations",
) =>
  request(
    scope(`/api/operations/configs/${serverId}`, agentId),
    json("DELETE", { revision, settings_revision: settingsRevision, confirmed: true }),
  );

const scope = (path: string, agentId: string) => `${path}?agent_id=${encodeURIComponent(agentId)}`;

export const deleteAgentSSH = (id: string, agentId: string, revision: number, serverRevision: number) =>
  request<SSHServer>(
    sshPath(id, agentId),
    json("DELETE", { revision, server_revision: serverRevision, confirmed: true }),
  );
