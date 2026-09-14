import { request } from './api';

export type AgentName = 'search' | 'code' | 'operations';
export const agentNames: Record<AgentName, string> = { search: 'Search agent', code: 'Code search agent', operations: 'SSH agent' };
export interface AgentPrompt { agent: string; active_version_id: string; revision: number }
export interface PromptVersion {
  id: string; agent: string; version: number; title: string; content: string;
  revision: number; status: 'draft' | 'active' | 'archived'; created_at: string; updated_at: string;
}
export interface Prompts { agents: AgentPrompt[]; versions: PromptVersion[] }
export const getPrompts = () => request<Prompts>('/api/llm/prompts');
export const savePrompt = (agent: string, title: string, content: string, version?: PromptVersion) =>
  request<PromptVersion>(`/api/llm/prompts/${agent}/versions${version ? `/${version.id}` : ''}`, {
    method: version ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, content, ...(version ? { revision: version.revision } : {}) }),
  });
export const activatePrompt = (version: PromptVersion, prompt: AgentPrompt) =>
  request<PromptVersion>(`/api/llm/prompts/${version.agent}/versions/${version.id}/activate`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ revision: version.revision, prompt_revision: prompt.revision }),
  });
export const deletePrompt = (version: PromptVersion, prompt: AgentPrompt) =>
  request<void>(`/api/llm/prompts/${version.agent}/versions/${version.id}`, {
    method: 'DELETE', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ revision: version.revision, prompt_revision: prompt.revision, confirmed: true }),
  });
