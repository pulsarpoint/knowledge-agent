import { request } from './api';
import type { ModelProfile } from './llm';
import type { AgentRuntime, RunResourcesInput } from './profiles';

/** The server's Claude connection rule (knowledge_llm.claude_model). */
export const claudeModel = (model: { provider: string; api_format: string; model: string }) =>
  model.api_format === 'native' && (model.provider === 'anthropic' || model.provider === 'bedrock' && model.model.includes('anthropic.claude'));
export const CATALOG_TOOLS_NEXT = 'Claude Code agents get these tools in the next update. What you save here is already part of the agent’s configuration and versions.';
/** Notes on the agent page tabs whose catalog tools Claude Code agents do not have yet. */
export const claudeCodeNotes: Partial<Record<string, string>> = {
  documents: CATALOG_TOOLS_NEXT, ssh: CATALOG_TOOLS_NEXT, mcp: CATALOG_TOOLS_NEXT, services: CATALOG_TOOLS_NEXT,
  git: 'Claude Code sessions open these code projects as read-only working folders. Code search and the code graph come in the next update.',
};
export const formatTokens = (value: number) => value.toLocaleString('en-US');
/** A session's agent type; sessions read before migration 0074 have none and are Claude Code sessions. */
export const sessionTypeLabel = (runtime?: AgentRuntime) => runtime === 'openai' ? 'Native' : 'Claude Code';
const json = (method: string, value: unknown) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) });

export type SessionStatus = 'starting' | 'working' | 'waiting' | 'stopped' | 'failed' | 'closed';
export const statusLabels: Record<SessionStatus, string> = {
  starting: 'Starting', working: 'Working', waiting: 'Waiting for you', stopped: 'Stopped', failed: 'Failed', closed: 'Finished',
};
export interface SessionFolder { path: string; kind: 'code_project' | 'folder'; name: string; code_project_id?: string }
export interface SessionSummary {
  id: string; title: string; runtime?: AgentRuntime; agent_id: string; agent_name: string; model_name: string; status: SessionStatus; status_message: string;
  turns: number; input_tokens: number; output_tokens: number; cached_tokens: number; cost_usd: number;
  document_id: string | null; document_title: string | null; folder: string; worker_active: boolean; queued: number;
  created_at: string; last_activity_at: string; closed_at: string | null;
}
export interface SessionDetail extends SessionSummary {
  document_name: string; draft: string | null; draft_turn: number | null; model_id: string;
  /** The agent's own instructions when the session started (earlier sessions also hold their documentation prompt). */
  prompt_snapshot: { system_prompt: string; claude_code_tools?: string[]; agent_version?: { id: string; version: number; label: string } | null };
  /** What to document and the working folders (earlier sessions hold servers and notes instead of the new keys). */
  context_snapshot: { folders: SessionFolder[]; code_project_ids: string[]; instructions?: string };
  /** What a per-run agent was given for this session (null: an agent with fixed resources). */
  run_resources?: RunResourcesInput | null;
  inputs: { seq: number; text: string; status: 'queued' | 'delivered' | 'discarded'; turn: number | null; created_at: string }[];
}
export type SessionState = Pick<SessionDetail, 'status' | 'status_message' | 'turns' | 'input_tokens' | 'output_tokens' | 'cached_tokens'
  | 'cost_usd' | 'draft' | 'draft_turn' | 'document_id' | 'worker_active' | 'queued'>;
export interface SessionEvent { id: number; turn: number; seq: number; kind: string; payload: Record<string, unknown>; created_at: string }
/** Docs → Generate documents → New: the agent runs with its own model, prompt, instructions, skills and tools. */
export interface SessionInput {
  agent_id: string; code_project_ids: string[]; collection_ids: string[]; environment_ids: string[]; service_ids: string[]; server_ids: string[];
  instructions: string; document_name: string; folder: string;
}
export interface SavedDocument { id: string; revision: number; version_id: string; folder: string; output_folder: string | null; export_error: string | null }

const session = (id: string) => `/api/claude-code/sessions/${encodeURIComponent(id)}`;
export const createSession = (value: SessionInput) => request<SessionDetail>('/api/claude-code/sessions', json('POST', value));
export const getSessions = (offset = 0, signal?: AbortSignal) => request<{ sessions: SessionSummary[] }>(`/api/claude-code/sessions?offset=${offset}`, { signal });
export const getSession = (id: string, signal?: AbortSignal) => request<SessionDetail>(session(id), { signal });
/** Events per page; a session page opens with the latest page and loads earlier pages as you scroll up. */
export const HISTORY_PAGE = 100;
/** The live stream after an event id (0: from the beginning); on a reconnect the browser resumes from the last id. */
export const sessionEventsUrl = (id: string, after = 0) => `${session(id)}/events?after=${after}`;
export const getSessionHistory = (id: string, before?: number, signal?: AbortSignal) =>
  request<{ events: SessionEvent[]; has_more: boolean }>(`${session(id)}/events/history?${before ? `before=${before}&` : ''}limit=${HISTORY_PAGE}`, { signal });
export const sendMessage = (id: string, text: string) => request<{ seq: number; status: 'queued'; working: boolean }>(`${session(id)}/messages`, json('POST', { text }));
export const stopSession = (id: string) => request<{ id: string; discarded: number }>(`${session(id)}/stop`, { method: 'POST' });
export const closeSession = (id: string) => request<{ id: string }>(`${session(id)}/close`, { method: 'POST' });
export const saveSessionDocument = (id: string, updateExisting: boolean) =>
  request<SavedDocument>(`${session(id)}/document`, json('POST', { update_existing: updateExisting }));
