import type { MCPServer } from './mcp';
import { ApiError, request } from './api';
import { parseSse } from './sse';
export interface SSHServer {
  id: string; name: string; hostname: string; port: number; username: string | null; revision: number;
  server_revision?: number; connection_source?: 'server' | 'agent';
  ssh_allowed: boolean; enabled: boolean; allow_sudo: boolean; known_hosts: string;
  has_private_key: boolean; last_tested_at: string | null; last_test_error: string | null;
}
export interface SSHInput { revision: number; server_revision?: number; username: string | null; port: number; allow_sudo: boolean; known_hosts: string; private_key?: string | null }
export interface SSHTestResult { ok: boolean; message: string; host_key: { public_key: string; fingerprint: string; algorithm: string; known_hosts_line: string; verified: boolean } | null }
export interface OperationsSettings { revision: number; instructions: string; server_ids: string[]; skill_ids: string[]; mcp_server_ids: string[] }
export interface OperationsServerConfig { id: string; server_id: string | null; revision: number; instructions: string | null; skill_ids: string[] | null; mcp_server_ids: string[] | null }
export interface ServerConfigInput { settings_revision: number; revision: number | null; instructions: string | null; skill_ids: string[] | null; mcp_server_ids: string[] | null }
export interface OperationsConfiguration { settings: OperationsSettings; mcp_servers: MCPServer[]; configs: OperationsServerConfig[]; servers: SSHServer[]; skills: { id: string; name: string; description: string; enabled: boolean }[] }
export interface RunSummary { id: string; session_id?: string | null; task: string; status: 'running' | 'completed' | 'failed' | 'cancelled'; created_at: string; actor_email: string }
export interface CommandResult { id: string; server_name: string; username: string; command: string; as_root: boolean; status: string; exit_code: number | null; stdout: string; stderr: string; error: string }
export interface RunActivity { id: string; kind: string; title: string; detail: string; status: string; time: string }
export interface RunUsage { requests: number; input_tokens: number; output_tokens: number; total_tokens: number; input_tokens_details?: { cached_tokens?: number; cache_write_tokens?: number }; output_tokens_details?: { reasoning_tokens?: number } }
export interface OperationsRun extends RunSummary { answer: string; error: string; progress: string; commands: CommandResult[]; context: { config?: Record<string, unknown>; servers: { name: string }[]; model_name: string; prompt: { version: number }; activity?: RunActivity[]; usage?: RunUsage | null; grounding?: { sources: { n: number; path: string; heading_path: string; anchor: string | null }[]; not_covered: boolean } | null } }
export interface AgentSession { agent_version_id?: string | null; config_version?: { id: string; version: number; label: string }; id: string; is_test: boolean; title: string; status: 'active' | 'closed' | 'expired'; created_at: string; last_activity_at: string; expires_at: string }
export interface SessionReview { id: string; response_id?: string | null; comment: string; suggestion: string; created_at: string }
export interface AgentConversation extends AgentSession { config: Record<string, unknown>; runs: OperationsRun[]; reviews: SessionReview[] }
export type RunEvent = { event: 'started' | 'completed'; data: OperationsRun } | { event: 'activity'; data: RunActivity };
const json = (method: string, value?: unknown) => ({ method, headers: { 'Content-Type': 'application/json' }, ...(value === undefined ? {} : { body: JSON.stringify(value) }) });
const sshPath = (id: string, agentId?: string) => agentId ? `/api/agents/${encodeURIComponent(agentId)}/servers/${id}/ssh` : `/api/servers/${id}/ssh`;
export const getSSH = (id: string, agentId?: string) => request<SSHServer>(sshPath(id, agentId));
export const saveSSH = (id: string, value: SSHInput, agentId?: string) => request<SSHServer>(sshPath(id, agentId), json('PUT', value));
export const testSSH = (id: string, value: SSHInput, agentId?: string) => request<SSHTestResult>(sshPath(id, agentId) + '/test', json('POST', value));
export const getOperationsSettings = (agentId = "operations") => request<OperationsConfiguration>(scope('/api/operations/settings', agentId));
export const saveOperationsSettings = (s: OperationsSettings, agentId = "operations") => request<OperationsSettings>(scope('/api/operations/settings', agentId), json('PUT', { revision: s.revision, instructions: s.instructions, server_ids: s.server_ids, skill_ids: s.skill_ids, mcp_server_ids: s.mcp_server_ids }));
const runsPath = (agentId: string) => `/api/agents/${encodeURIComponent(agentId)}/runs`;
export const getOperationsRuns = (agentId = "operations") => request<{ runs: RunSummary[] }>(runsPath(agentId));
export const getOperationsRun = (id: string, agentId = "operations") => request<OperationsRun>(`${runsPath(agentId)}/${id}`);
export async function startOperationsRun(task: string, agentId: string, signal: AbortSignal, onEvent: (event: RunEvent) => void, sessionId?: string, versionId?: string) {
  const response = await fetch(runsPath(agentId), { ...json('POST', { id: crypto.randomUUID(), task, session_id: sessionId, version_id: versionId, is_test: true }), signal });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new ApiError(body?.error?.message ?? `Could not start the agent (${response.status}).`, response.status);
  }
  if (!response.body) throw new ApiError('The agent response stream is unavailable.', 502);
  let completed = false;
  for await (const event of parseSse(response.body)) {
    if (event.event === 'error') throw new ApiError((event.data as { message: string }).message, 409);
    if (event.event === 'started' || event.event === 'activity' || event.event === 'completed') {
      onEvent(event as RunEvent);
      if (event.event === 'completed') completed = true;
    }
  }
  if (!completed) throw new ApiError('The live connection closed. Reopen the session to follow its progress; the task was not retried.', 502);
}
export const cancelOperationsRun = (id: string, agentId = "operations") => request<{ id: string }>(`${runsPath(agentId)}/${id}/cancel`, json('POST'));
const sessionsPath = (agentId: string) => `/api/agents/${encodeURIComponent(agentId)}/sessions`;
export const getAgentSessions = (agentId: string) => request<{ sessions: AgentSession[] }>(`${sessionsPath(agentId)}?is_test=true`);
export const getAgentSession = (agentId: string, sessionId: string) => request<AgentConversation>(`${sessionsPath(agentId)}/${encodeURIComponent(sessionId)}`);
export const closeAgentSession = (agentId: string, sessionId: string) => request(`${sessionsPath(agentId)}/${encodeURIComponent(sessionId)}/close`, json('POST'));
export const reviewAgentSession = (agentId: string, sessionId: string, comment: string, responseId?: string) => request<SessionReview>(`${sessionsPath(agentId)}/${encodeURIComponent(sessionId)}/review`, json('POST', { comment, response_id: responseId }));

export const saveOperationsServerConfig = (serverId: string, config: ServerConfigInput, agentId = "operations") => request<OperationsServerConfig>(scope(`/api/operations/configs/${serverId}`, agentId), json('PUT', config));
export const deleteOperationsServerConfig = (serverId: string, revision: number, settingsRevision: number, agentId = "operations") => request(scope(`/api/operations/configs/${serverId}`, agentId), json('DELETE', { revision, settings_revision: settingsRevision, confirmed: true }));

const scope = (path: string, agentId: string) => `${path}?agent_id=${encodeURIComponent(agentId)}`;

export const deleteAgentSSH = (id: string, agentId: string, revision: number, serverRevision: number) => request<SSHServer>(sshPath(id, agentId), json('DELETE', { revision, server_revision: serverRevision, confirmed: true }));
