import { request } from './api';
import type { ToolCount } from './runs';
import type { RunResourcesInput } from './profiles';
import type { SessionReview } from './testSessions';

export const formatTokens = (value: number) => value.toLocaleString('en-US');
const json = (method: string, value: unknown) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) });

export type SessionPurpose = 'documents' | 'test' | 'event' | 'discovery' | 'revision';
export type SessionStatus = 'starting' | 'working' | 'waiting' | 'stopped' | 'failed' | 'closed';
export const statusLabels: Record<SessionStatus, string> = {
  starting: 'Starting', working: 'Working', waiting: 'Waiting for you', stopped: 'Stopped', failed: 'Failed', closed: 'Finished',
};
export interface SessionFolder { path: string; kind: 'code_project' | 'folder'; name: string; code_project_id?: string }
export interface SessionSummary {
  id: string; title: string; agent_id: string | null; agent_name: string; model_name: string; status: SessionStatus; status_message: string;
  turns: number; input_tokens: number; output_tokens: number; cached_tokens: number;
  purpose: SessionPurpose; model_calls: number; reasoning_tokens: number;
  document_id: string | null; document_title: string | null; folder: string | null;  // null for a Test session
  worker_active: boolean; queued: number;
  created_at: string; last_activity_at: string; closed_at: string | null;
}
export interface SessionDetail extends SessionSummary {
  /** Ask agent runs: the document they revise. */
  target_document_id?: string | null;
  tool_counts?: ToolCount[];
  /** The id of the last tool_call event the counts include (0: none). */
  tool_counts_through_id?: number;
  server_id?: string | null; document_name: string | null; draft: string | null; draft_turn: number | null; model_id: string;
  /** What the session started with. Test sessions: the agent's own system prompt (earlier sessions also hold their
   *  documentation prompt; only sessions saved before migration 0080 may carry `agent_version`). Generate documents
   *  sessions: the user's system prompt, the context they generated or edited, and the saved prompt chosen (null: their own). */
  prompt_snapshot: { system_prompt: string; context?: string; prompt_id?: string | null;
    agent_version?: { id: string; version: number; label: string } | null };
  /** What to document and the working folders (earlier sessions hold servers and notes instead of the new keys). */
  context_snapshot: { folders: SessionFolder[]; code_project_ids: string[]; instructions?: string };
  /** What a per-run agent was given for this session (null: an agent with fixed resources). */
  run_resources?: RunResourcesInput | null;
  /** Test sessions: the configuration pinned when the session started (credential references only). */
  config?: Record<string, unknown>;
  /** Test sessions: their saved suggestions, oldest first. */
  reviews?: SessionReview[];
  /** The run's latest document draft of any status (Generate documents); null: the run never had one. */
  document_draft?: { id: string; document_id: string | null; status: 'open' | 'promoted' | 'discarded' } | null;
  inputs: { seq: number; text: string; status: 'queued' | 'delivered' | 'discarded'; turn: number | null; created_at: string }[];
}
export type SessionState = Pick<SessionDetail, 'status' | 'status_message' | 'turns' | 'input_tokens' | 'output_tokens' | 'cached_tokens'
  | 'draft' | 'draft_turn' | 'document_id' | 'document_draft' | 'worker_active' | 'queued' | 'model_calls' | 'reasoning_tokens'>;
export interface SessionEvent { id: number; turn: number; seq: number; kind: string; payload: Record<string, unknown>; created_at: string }
export const DEFAULT_SYSTEM_PROMPT = 'You write technical documentation for software systems. Read the code, documents and servers you were given before you write. Cite evidence as file:line, document path or command. Say what you could not verify.\n\nWhen the document covers several parts (services, code projects, servers), plan the sections first. For each part, ask an expert when its description fits; otherwise start a subagent with run_subagent. Give each a complete task: the section\'s purpose, what to cover, and only that part\'s resources. Write small single-part documents yourself. Combine the answers into one consistent document; check that they agree and resolve or mark conflicts.';
/** What a generation documents. */
export interface Selection { code_project_ids: string[]; collection_ids: string[]; environment_ids: string[]; service_ids: string[]; server_ids: string[] }
/** Docs → Generate documents → New: an ad-hoc native agent with this model, system prompt and context; the prompt is its first message. */
export interface SessionInput extends Selection {
  model_id: string; system_prompt: string; context: string; prompt: string; prompt_id: string | null; document_name: string; folder: string;
}

const session = (id: string) => `/api/generations/sessions/${encodeURIComponent(id)}`;
export const createSession = (value: SessionInput) => request<SessionDetail>('/api/generations/sessions', json('POST', value));
export const getGenerationContext = (selection: Selection, signal?: AbortSignal) =>
  request<{ text: string }>('/api/generations/context', { ...json('POST', selection), signal });
export const getSessions = (offset = 0, signal?: AbortSignal) => request<{ sessions: SessionSummary[] }>(`/api/generations/sessions?offset=${offset}`, { signal });
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
/** Save as draft (runs from before document drafts): the run's draft becomes a document draft; nothing goes live. */
export const saveSessionDraft = (id: string) =>
  request<{ draft: NonNullable<SessionDetail['document_draft']> }>(`${session(id)}/document`, json('POST', {}));
