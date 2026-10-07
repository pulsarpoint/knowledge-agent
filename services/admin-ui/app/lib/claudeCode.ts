import { request } from './api';
import type { ModelProfile } from './llm';

export const CLAUDE_CODE_SDK = 'Claude Code SDK';
export const CLAUDE_CODE_TOOLS = 'Read, Grep, Glob, Bash';
/** The server's Claude connection rule (knowledge_llm.claude_model). */
export const claudeModel = (model: Pick<ModelProfile, 'provider' | 'api_format' | 'model'>) =>
  model.api_format === 'native' && (model.provider === 'anthropic' || model.provider === 'bedrock' && model.model.includes('anthropic.claude'));
export const formatTokens = (value: number) => value.toLocaleString('en-US');
const json = (method: string, value: unknown) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) });

export interface ClaudeCodeAgentInput { name: string; description: string; revision?: number }
export interface ClaudeCodeAgent { id: string; name: string; description: string; revision: number }
export const createClaudeCodeAgent = (value: ClaudeCodeAgentInput) => request<{ id: string }>('/api/claude-code/agents', json('POST', value));
export const saveClaudeCodeAgent = (id: string, value: ClaudeCodeAgentInput) =>
  request<ClaudeCodeAgent>(`/api/claude-code/agents/${encodeURIComponent(id)}`, json('PUT', value));

export type SessionStatus = 'starting' | 'working' | 'waiting' | 'stopped' | 'failed' | 'closed';
export const statusLabels: Record<SessionStatus, string> = {
  starting: 'Starting', working: 'Working', waiting: 'Waiting for you', stopped: 'Stopped', failed: 'Failed', closed: 'Finished',
};
export interface SessionFolder { path: string; kind: 'code_project' | 'folder'; name: string; code_project_id?: string }
export interface SessionSummary {
  id: string; title: string; agent_id: string; agent_name: string; model_name: string; status: SessionStatus; status_message: string;
  turns: number; input_tokens: number; output_tokens: number; cached_tokens: number; cost_usd: number;
  document_id: string | null; document_title: string | null; folder: string; worker_active: boolean; queued: number;
  created_at: string; last_activity_at: string; closed_at: string | null;
}
export interface SessionDetail extends SessionSummary {
  document_name: string; draft: string | null; draft_turn: number | null; model_id: string;
  prompt_snapshot: { id: string; name: string; version: number; comments: string; system_prompt: string };
  context_snapshot: { folders: SessionFolder[]; code_project_ids: string[]; servers: { id: string; name: string; hostname: string; description: string }[]; notes: string };
  inputs: { seq: number; text: string; status: 'queued' | 'delivered' | 'discarded'; turn: number | null; created_at: string }[];
}
export type SessionState = Pick<SessionDetail, 'status' | 'status_message' | 'turns' | 'input_tokens' | 'output_tokens' | 'cached_tokens'
  | 'cost_usd' | 'draft' | 'draft_turn' | 'document_id' | 'worker_active' | 'queued'>;
export interface SessionEvent { id: number; turn: number; seq: number; kind: string; payload: Record<string, unknown>; created_at: string }
export interface SessionInput {
  agent_id: string; model_id: string; prompt_id: string; prompt_version_id: string; comments: string;
  code_project_ids: string[]; folders: string[]; server_ids: string[]; notes: string; document_name: string; folder: string;
}
export interface SavedDocument { id: string; revision: number; version_id: string; folder: string; output_folder: string | null; export_error: string | null }

const session = (id: string) => `/api/claude-code/sessions/${encodeURIComponent(id)}`;
export const createSession = (value: SessionInput) => request<SessionDetail>('/api/claude-code/sessions', json('POST', value));
export const getSessions = (offset = 0, signal?: AbortSignal) => request<{ sessions: SessionSummary[] }>(`/api/claude-code/sessions?offset=${offset}`, { signal });
export const getSession = (id: string, signal?: AbortSignal) => request<SessionDetail>(session(id), { signal });
export const sessionEventsUrl = (id: string) => `${session(id)}/events`;
export const sendMessage = (id: string, text: string) => request<{ seq: number; status: 'queued'; working: boolean }>(`${session(id)}/messages`, json('POST', { text }));
export const stopSession = (id: string) => request<{ id: string; discarded: number }>(`${session(id)}/stop`, { method: 'POST' });
export const closeSession = (id: string) => request<{ id: string }>(`${session(id)}/close`, { method: 'POST' });
export const saveSessionDocument = (id: string, updateExisting: boolean) =>
  request<SavedDocument>(`${session(id)}/document`, json('POST', { update_existing: updateExisting }));
