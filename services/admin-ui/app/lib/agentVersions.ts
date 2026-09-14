import { request } from './api';
export interface AgentVersion { id: string; version: number; label: string; notes: string; status: 'draft' | 'active' | 'archived'; created_at: string; published_at: string | null; source_review_id: string | null }
export interface AgentVersions { active_id: string | null; versions: AgentVersion[] }
export interface InstructionChanges { system_prompt: string | null; instructions: string | null; server_instructions: { server_id: string; instructions: string }[] }
export interface VersionConfig extends Record<string, unknown> {
  system_prompt: { content: string }; instructions: string;
  ssh: { enabled: boolean; servers: { id: string; name: string; instructions: string | null; instructions_source?: string }[] };
}
export interface DraftSource { config: VersionConfig; source_hash: string; changes: InstructionChanges; suggestion: string }
const path = (agentId: string) => `/api/agents/${encodeURIComponent(agentId)}/versions`;
const json = (value: unknown) => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) });
export const getAgentVersions = (agentId: string) => request<AgentVersions>(path(agentId));
export const getVersionSource = (agentId: string, source: { version_id?: string; review_id?: string } = {}) => request<DraftSource>(`${path(agentId)}/source?${new URLSearchParams(source)}`);
export const createAgentVersion = (agentId: string, value: { label: string; notes: string; source_hash: string; source_version_id?: string; source_review_id?: string; changes: InstructionChanges }) => request<Pick<AgentVersion,'id'|'version'|'label'>>(path(agentId), json(value));
export const activateAgentVersion = (agentId: string, versionId: string, activeId: string | null) => request(`${path(agentId)}/${versionId}/activate`, json({ expected_active_id: activeId }));
