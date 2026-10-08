import { request } from './api';
export type AgentRuntime = 'openai' | 'claude_code';
export const AGENT_TYPES: [AgentRuntime, string][] = [['openai', 'Native'], ['claude_code', 'Claude Code']];
export const agentTypeLabel = (runtime?: AgentRuntime) => runtime === 'claude_code' ? 'Claude Code' : 'Native';
export const AGENT_TYPE_HELP: Record<AgentRuntime, string> = {
  openai: 'Runs on the OpenAI Agents SDK with the tools enabled in its tabs.',
  claude_code: 'Runs on the Claude Agent SDK (Claude Code) in its own container. Needs a Claude model (Bedrock or Anthropic).',
};
export type AgentResources = 'fixed' | 'per_run';
export const RESOURCE_MODES: [AgentResources, string][] = [['fixed', 'Fixed'], ['per_run', 'Per run']];
export interface ResourceLimit { kind: 'code' | 'documents' | 'servers' | 'services'; limit: number | 'any' }
/** "Fixed", or what a per-run agent accepts: "Per run · code (any) · servers (2)". */
export const resourcesSummary = (agent: { resources?: AgentResources; accepts?: ResourceLimit[] }) =>
  agent.resources !== 'per_run' ? 'Fixed'
    : agent.accepts?.length ? ['Per run', ...agent.accepts.map(({ kind, limit }) => `${kind} (${limit})`)].join(' · ') : 'Per run · nothing';
/** Notes on the capability tabs of a per-run agent: its selections are what callers may pass. */
export const PER_RUN_NOTES: Partial<Record<string, string>> = {
  git: 'Per run: callers may pass any of the code projects selected here. All code projects = any code project.',
  documents: 'Per run: callers may pass any of the collections selected here. All collections = any collection.',
  ssh: 'Per run: callers may pass any of the servers selected here. Their instructions and connection overrides still apply.',
  services: 'Per run: callers may pass any of the services selected here.',
};
export const resourcesLabel = (mode?: AgentResources) => mode === 'per_run' ? 'Per run' : 'Fixed';
export const RESOURCES_HELP: Record<AgentResources, string> = {
  fixed: 'Always works with the resources selected in its tabs.',
  per_run: 'Each run says what to work on: callers pass code projects, collections, servers or services from those selected in its tabs.',
};
export interface RunResourceItem { id: string; name: string }
export type RunResourceKind = 'code_projects' | 'collections' | 'servers' | 'services';
/** What a caller may pass to one run of a per-run agent; null: a kind the agent does not use. */
export type RunResourceOptions = { resources: AgentResources } & Partial<Record<RunResourceKind, RunResourceItem[] | null>>;
export interface RunResourcesInput { code_project_ids: string[]; collection_ids: string[]; server_ids: string[]; service_ids: string[] }
export const NO_RUN_RESOURCES: RunResourcesInput = { code_project_ids: [], collection_ids: [], server_ids: [], service_ids: [] };
export const RUN_RESOURCE_KINDS: [RunResourceKind, keyof RunResourcesInput, string][] = [
  ['code_projects', 'code_project_ids', 'Code projects'], ['collections', 'collection_ids', 'Collections'],
  ['servers', 'server_ids', 'Servers'], ['services', 'service_ids', 'Services']];
export const CLAUDE_CODE_TOOL_NAMES = ['Read', 'Grep', 'Glob', 'Bash'] as const;
export type ClaudeCodeTool = typeof CLAUDE_CODE_TOOL_NAMES[number];
export interface AgentProfile {
  id: string; name: string; description: string; when_to_use: string; enabled: boolean;
  runtime?: AgentRuntime; claude_code_tools?: ClaudeCodeTool[]; resources?: AgentResources;
  documents_enabled: boolean; repositories_enabled: boolean; ssh_enabled: boolean; skills_enabled: boolean; mcp_enabled: boolean; services_enabled: boolean; documents_grounded: boolean;
  active_config_version_id?: string | null;
  revision: number; model_id: string | null; all_collections: boolean; all_repositories: boolean;
  collection_ids: string[]; repository_ids: string[]; service_ids: string[]; skill_ids: string[];
  active_prompt: { id: string; title: string; version: number } | null;
}
export interface AgentSummary extends AgentProfile { accepts?: ResourceLimit[]; config_version?: number | null; model_name: string | null; prompt_title: string | null; prompt_version: number | null }
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
export const getRunResourceOptions = (id: string, versionId?: string) =>
  request<RunResourceOptions>(path(id) + '/run-resources' + (versionId ? `?version_id=${encodeURIComponent(versionId)}` : ''));
export const saveAgent = (value: AgentProfile, skillIds?: string[]) => {
  const { revision, name, description, when_to_use, enabled, all_collections, all_repositories, collection_ids, repository_ids, service_ids, documents_enabled, repositories_enabled, ssh_enabled, skills_enabled, mcp_enabled, services_enabled, documents_grounded, runtime, claude_code_tools, resources } = value;
  return request<AgentProfile>(path(value.id), json('PUT', { ...(skillIds === undefined ? {} : { skill_ids: skillIds }), revision, name, description, when_to_use, enabled, all_collections, all_repositories, collection_ids, repository_ids, service_ids, documents_enabled, repositories_enabled, ssh_enabled, skills_enabled, mcp_enabled, services_enabled, documents_grounded,
    ...(runtime ? { runtime } : {}), ...(resources ? { resources } : {}), ...(claude_code_tools ? { claude_code_tools } : {}) }));
};
export const saveProfileModel = (value: AgentProfile, model_id: string | null) => request<AgentProfile>(path(value.id) + '/model', json('PUT', { revision: value.revision, model_id }));

export const deleteAgent = (id: string, revision: number, withHistory = false) =>
  request<void>(path(id), json('DELETE', { revision, confirmed: true, ...(withHistory ? { with_history: true } : {}) }));
/** What deleting an agent with its history removes; saved documents are kept. */
export interface AgentHistory { test_chats: number; chats: number; claude_code_sessions: number; runs: number }
export const getAgentHistory = (id: string) => request<AgentHistory>(path(id) + '/history');
const HISTORY_LABELS: [keyof AgentHistory, string, string][] = [
  ['test_chats', 'test chat', 'test chats'], ['chats', 'chat', 'chats'], ['claude_code_sessions', 'Claude Code session', 'Claude Code sessions'],
  ['runs', 'run', 'runs'],
];
/** "2 test chats, 1 Claude Code session, 3 runs"; empty when the agent has no history. */
export const describeHistory = (history: AgentHistory) =>
  HISTORY_LABELS.filter(([key]) => history[key] > 0).map(([key, one, many]) => `${history[key]} ${history[key] === 1 ? one : many}`).join(', ');

