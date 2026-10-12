import { request } from './api';
import type { SessionDetail } from './generations';

const json = (method: string, value: unknown) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) });

export interface AgentResource { id: string; name: string; linked: boolean }
export interface AgentServer extends AgentResource { usable: boolean; reason: string | null }
export interface AgentTool { name: string; label: string; summary: string; group: string; needs: string | null; requires: string | null }
export interface AgentOptions {
  models: { id: string; name: string }[]; default_model_id: string | null;
  prompts: { id: string; name: string; content: string }[]; default_prompt: { id: null; name: string; content: string };
  resources: { servers: AgentServer[]; services: AgentResource[]; environments: AgentResource[]; code_projects: AgentResource[]; collections: AgentResource[] };
  tools: AgentTool[]; fixed_tools: { name: string; label: string }[]; experts: { id: string; name: string; when_to_use: string }[];
}
export interface AgentResources { server_ids: string[]; service_ids: string[]; environment_ids: string[]; code_project_ids: string[]; collection_ids: string[] }
export interface StartInput {
  kind: 'revision'; target: { document_id: string }; instruction: string; model_id: string; prompt_id: string | null;
  system_prompt: string; resources: AgentResources; tools: string[]; expert_ids: string[]; subagents: boolean;
}
export interface AgentRunSummary {
  id: string; title: string; status: SessionDetail['status']; model_name: string; started_by: string | null;
  created_at: string; last_activity_at: string; draft_id: string | null;
}

export const getAgentOptions = (documentId: string, signal?: AbortSignal) =>
  request<AgentOptions>(`/api/agent-sessions/options?kind=revision&document_id=${encodeURIComponent(documentId)}`, { signal });
export const previewAgentSession = (value: StartInput) =>
  request<{ config: Record<string, unknown>; first_message: string }>('/api/agent-sessions/preview', json('POST', value));
export const startAgentSession = (value: StartInput) => request<SessionDetail>('/api/agent-sessions', json('POST', value));
export const getDocumentAgentRuns = (documentId: string, signal?: AbortSignal) =>
  request<{ sessions: AgentRunSummary[] }>(`/api/agent-sessions?document_id=${encodeURIComponent(documentId)}`, { signal });
export const agentRunPath = (documentId: string, runId: string) =>
  `/documents/${encodeURIComponent(documentId)}/agent/${encodeURIComponent(runId)}`;

/** Which tool groups apply: a tool shows when what it needs is selected (inventory and other no-need tools always). */
export function toolApplies(tool: AgentTool, chosen: AgentResources): boolean {
  const code = chosen.code_project_ids.length > 0 || chosen.service_ids.length > 0 || chosen.environment_ids.length > 0;
  switch (tool.needs) {
    case null: return true;
    case 'ssh': return chosen.server_ids.length > 0 || chosen.service_ids.length > 0 || chosen.environment_ids.length > 0;
    case 'documents': return chosen.collection_ids.length > 0;
    case 'files': return chosen.code_project_ids.length > 0 || chosen.collection_ids.length > 0;  // matches `requires`: a code project or a collection
    default: return code;  // code, code_graph
  }
}
