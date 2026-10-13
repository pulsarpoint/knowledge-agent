import { request } from './api';
import type { ToolsSummary } from './tools';
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
export interface RunResourcesInput { code_project_ids: string[]; collection_ids: string[]; server_ids: string[]; service_ids: string[] }
export interface AgentProfile {
  id: string; name: string; description: string; when_to_use: string; enabled: boolean;
  resources?: AgentResources; subagents?: boolean; expert?: boolean;
  documents_enabled: boolean; repositories_enabled: boolean; ssh_enabled: boolean; skills_enabled: boolean; mcp_enabled: boolean; services_enabled: boolean; documents_grounded: boolean;
  revision: number; model_id: string | null; all_collections: boolean; all_repositories: boolean;
  collection_ids: string[]; repository_ids: string[]; service_ids: string[]; skill_ids: string[];
  system_prompt: string; cloned_from?: string | null; original?: { id: string; name: string } | null;
}
export interface AgentSummary extends AgentProfile { accepts?: ResourceLimit[]; model_name: string | null; original_name?: string | null; tools_summary?: ToolsSummary }
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
export const getAgentConfig = (id: string) => request<Record<string, unknown>>(path(id) + '/config');
export const getAgentOptions = (id: string) => request<AgentOptions>(path(id) + '/options');
export const saveAgent = (value: AgentProfile, skillIds?: string[]) => {
  const { revision, name, description, when_to_use, enabled, all_collections, all_repositories, collection_ids, repository_ids, service_ids, documents_enabled, repositories_enabled, ssh_enabled, skills_enabled, mcp_enabled, services_enabled, documents_grounded, resources, subagents, expert, model_id } = value;
  return request<AgentProfile>(path(value.id), json('PUT', { ...(skillIds === undefined ? {} : { skill_ids: skillIds }), revision, name, description, when_to_use, enabled, all_collections, all_repositories, collection_ids, repository_ids, service_ids, documents_enabled, repositories_enabled, ssh_enabled, skills_enabled, mcp_enabled, services_enabled, documents_grounded,
    ...(resources ? { resources } : {}),
    ...(subagents !== undefined ? { subagents } : {}), ...(expert !== undefined ? { expert } : {}), ...(model_id !== undefined ? { model_id } : {}) }));
};
export const saveAgentPrompt = (id: string, revision: number, system_prompt: string) =>
  request<AgentProfile>(path(id) + '/prompt', json('PUT', { revision, system_prompt }));
export const saveProfileModel = (value: AgentProfile, model_id: string | null) => request<AgentProfile>(path(value.id) + '/model', json('PUT', { revision: value.revision, model_id }));

export const deleteAgent = (id: string, revision: number, withHistory = false) =>
  request<void>(path(id), json('DELETE', { revision, confirmed: true, ...(withHistory ? { with_history: true } : {}) }));
/** What deleting an agent with its history removes; saved documents are kept. `run_records`: Test chats from before
 * the Test tab ran sessions. */
export interface AgentHistory { test_chats: number; documentation_sessions: number; run_records: number }
export const getAgentHistory = (id: string) => request<AgentHistory>(path(id) + '/history');
const HISTORY_LABELS: [keyof AgentHistory, string, string][] = [
  ['test_chats', 'test chat', 'test chats'], ['documentation_sessions', 'documentation session', 'documentation sessions'],
  ['run_records', 'run record', 'run records'],
];
/** "2 test chats, 1 documentation session, 3 run records"; empty when the agent has no history. */
export const describeHistory = (history: AgentHistory) =>
  HISTORY_LABELS.filter(([key]) => history[key] > 0).map(([key, one, many]) => `${history[key]} ${history[key] === 1 ? one : many}`).join(', ');

