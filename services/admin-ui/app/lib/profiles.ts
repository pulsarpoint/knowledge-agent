import { request } from './api';
export interface AgentProfile {
  id: string; name: string; description: string; when_to_use: string; enabled: boolean; is_coordinator: boolean;
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
  const { revision, name, description, when_to_use, enabled, all_collections, all_repositories, collection_ids, repository_ids, service_ids, documents_enabled, repositories_enabled, ssh_enabled, skills_enabled, mcp_enabled, services_enabled, documents_grounded } = value;
  return request<AgentProfile>(path(value.id), json('PUT', { ...(skillIds === undefined ? {} : { skill_ids: skillIds }), revision, name, description, when_to_use, enabled, all_collections, all_repositories, collection_ids, repository_ids, service_ids, documents_enabled, repositories_enabled, ssh_enabled, skills_enabled, mcp_enabled, services_enabled, documents_grounded }));
};
export const saveProfileModel = (value: AgentProfile, model_id: string | null) => request<AgentProfile>(path(value.id) + '/model', json('PUT', { revision: value.revision, model_id }));

export const deleteAgent = (id: string, revision: number) => request<void>(path(id), json('DELETE', { revision, confirmed: true }));

