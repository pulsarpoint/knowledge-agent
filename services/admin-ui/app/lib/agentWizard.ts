import { request } from './api';
import type { AgentResources, AgentRuntime, ClaudeCodeTool } from './profiles';

export interface WizardResource { id: string; name: string; description?: string }
export interface WizardConnection {
  username: string | null; port: number; allow_sudo: boolean; known_hosts: string; credential_id: string | null;
}
export interface WizardServer {
  server_id: string; instructions: string | null; skill_ids: string[] | null;
  mcp_server_ids: string[] | null; connection: WizardConnection | null;
}
export interface AgentProposal {
  name: string; description: string; when_to_use: string; system_prompt: string; instructions: string;
  model_id: string | null; enabled: boolean;
  documents_enabled: boolean; repositories_enabled: boolean; ssh_enabled: boolean;
  skills_enabled: boolean; mcp_enabled: boolean; services_enabled: boolean;
  all_collections: boolean; all_repositories: boolean;
  collection_ids: string[]; repository_ids: string[]; skill_ids: string[];
  mcp_server_ids: string[]; service_ids: string[]; servers: WizardServer[];
}
export interface WizardOptions {
  prompt: string; schema: Record<string, unknown>;
  catalog: {
    models: (WizardResource & { provider: string; model: string; api_format: string })[];
    code_projects: (WizardResource & { folder_paths: string[]; remote_url: string | null })[];
    collections: { id: string; name: string; description: string }[];
    servers: (WizardResource & WizardConnection & { hostname: string; tags: string[] })[];
    credentials: WizardResource[]; skills: WizardResource[]; mcp_servers: WizardResource[];
    services: WizardResource[]; existing_agents: { name: string; description: string; prompt_title: string }[];
  };
}
export type NewAgent = AgentProposal & { runtime: AgentRuntime; resources: AgentResources; claude_code_tools: ClaudeCodeTool[] };
export const getWizardOptions = () => request<WizardOptions>('/api/agents/wizard/options');
/** The designer proposes the agent's model only among models that fit the chosen agent type. */
export const generateAgent = (role: string, model_id: string, runtime: AgentRuntime) => request<{ proposal: AgentProposal }>('/api/agents/wizard/generate', {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ role, model_id, runtime }),
});
export const createWizardAgent = (value: NewAgent) => request<{ id: string }>('/api/agents/wizard', {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value),
});
