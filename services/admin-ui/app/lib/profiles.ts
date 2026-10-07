import { request } from './api';
export type AgentRuntime = 'openai' | 'claude_code';
export const AGENT_TYPES: [AgentRuntime, string][] = [['openai', 'Native'], ['claude_code', 'Claude Code']];
export const agentTypeLabel = (runtime?: AgentRuntime) => runtime === 'claude_code' ? 'Claude Code' : 'Native';
export const AGENT_TYPE_HELP: Record<AgentRuntime, string> = {
  openai: 'Runs on the OpenAI Agents SDK with the tools enabled in its tabs.',
  claude_code: 'Runs on the Claude Agent SDK (Claude Code) in its own container. Needs a Claude model (Bedrock or Anthropic).',
};
export const CLAUDE_CODE_TOOL_NAMES = ['Read', 'Grep', 'Glob', 'Bash'] as const;
export type ClaudeCodeTool = typeof CLAUDE_CODE_TOOL_NAMES[number];
export interface AgentProfile {
  id: string; name: string; description: string; when_to_use: string; enabled: boolean; is_coordinator: boolean;
  runtime?: AgentRuntime; claude_code_tools?: ClaudeCodeTool[];
  documents_enabled: boolean; repositories_enabled: boolean; ssh_enabled: boolean; skills_enabled: boolean; mcp_enabled: boolean; services_enabled: boolean; documents_grounded: boolean;
  active_config_version_id?: string | null;
  revision: number; model_id: string | null; all_collections: boolean; all_repositories: boolean;
  collection_ids: string[]; repository_ids: string[]; service_ids: string[]; skill_ids: string[];
  active_prompt: { id: string; title: string; version: number } | null;
}
export interface AgentSummary extends AgentProfile { config_version?: number | null; model_name: string | null; prompt_title: string | null; prompt_version: number | null }
export interface AgentOptions {
  collections: { id: string; name: string; description: string }[];
  repositories: { id: string; name: string }[];
  services: { id: string; name: string; description: string }[];
  skills: { id: string; name: string; description: string; enabled: boolean }[];
}
const json = (method: string, value: unknown) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) });
const path = (id: string) => `/api/agents/${encodeURIComponent(id)}`;
export const getAgents = () => request<{ agents: AgentSummary[] }>('/api/agents');
export const getAgent = (id: string) => request<AgentProfile>(path(id));
export const getAgentConfig = (id: string, versionId?: string) => request<Record<string, unknown>>(path(id) + '/config' + (versionId ? `?version_id=${encodeURIComponent(versionId)}` : ''));
export const getAgentOptions = (id: string) => request<AgentOptions>(path(id) + '/options');
export const saveAgent = (value: AgentProfile, skillIds?: string[]) => {
  const { revision, name, description, when_to_use, enabled, all_collections, all_repositories, collection_ids, repository_ids, service_ids, documents_enabled, repositories_enabled, ssh_enabled, skills_enabled, mcp_enabled, services_enabled, documents_grounded, runtime, claude_code_tools } = value;
  return request<AgentProfile>(path(value.id), json('PUT', { ...(skillIds === undefined ? {} : { skill_ids: skillIds }), revision, name, description, when_to_use, enabled, all_collections, all_repositories, collection_ids, repository_ids, service_ids, documents_enabled, repositories_enabled, ssh_enabled, skills_enabled, mcp_enabled, services_enabled, documents_grounded,
    ...(runtime ? { runtime } : {}), ...(claude_code_tools ? { claude_code_tools } : {}) }));
};
export const saveProfileModel = (value: AgentProfile, model_id: string | null) => request<AgentProfile>(path(value.id) + '/model', json('PUT', { revision: value.revision, model_id }));

export const deleteAgent = (id: string, revision: number, withHistory = false) =>
  request<void>(path(id), json('DELETE', { revision, confirmed: true, ...(withHistory ? { with_history: true } : {}) }));
/** What deleting an agent with its history removes; saved documents are kept. */
export interface AgentHistory { test_chats: number; chats: number; claude_code_sessions: number; runs: number; board_tasks: number; board_memberships: number }
export const getAgentHistory = (id: string) => request<AgentHistory>(path(id) + '/history');
const HISTORY_LABELS: [keyof AgentHistory, string, string][] = [
  ['test_chats', 'test chat', 'test chats'], ['chats', 'chat', 'chats'], ['claude_code_sessions', 'Claude Code session', 'Claude Code sessions'],
  ['runs', 'run', 'runs'], ['board_tasks', 'board task', 'board tasks'], ['board_memberships', 'board membership', 'board memberships'],
];
/** "2 test chats, 1 Claude Code session, 3 runs"; empty when the agent has no history. */
export const describeHistory = (history: AgentHistory) =>
  HISTORY_LABELS.filter(([key]) => history[key] > 0).map(([key, one, many]) => `${history[key]} ${history[key] === 1 ? one : many}`).join(', ');

