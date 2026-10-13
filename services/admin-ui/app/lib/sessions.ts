import { request } from './api';
import type { ToolCount } from './runs';
import type { RunResourcesInput } from './profiles';
import type { Turn } from './turns';

export const formatTokens = (value: number) => value.toLocaleString('en-US');
const json = (method: string, value: unknown) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) });

export type SessionPurpose = 'documents' | 'test' | 'event' | 'discovery' | 'revision';
export type SessionStatus = 'starting' | 'working' | 'waiting' | 'stopped' | 'failed' | 'closed';
export const statusLabels: Record<SessionStatus, string> = {
  starting: 'Starting', working: 'Working', waiting: 'Waiting for you', stopped: 'Stopped', failed: 'Failed', closed: 'Finished',
};
/** A tuning suggestion; `response_turn` is the answer the feedback was about (none: the whole session). */
export interface SessionReview { id: string; comment: string; suggestion: string; response_turn?: number | null; created_at: string; changes?: unknown }
/** What a chat is about: a document (Ask agent), a saved agent (its Test tab) or a new document (Documents → New: the
 * Title and Folder the editor holds now). */
export type ChatScope = { kind: 'document'; id: string; title: string } | { kind: 'agent'; id: string; name: string }
  | { kind: 'new-document'; folder: string; title: string } | { kind: 'server'; id: string; name: string };

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
  /** The run's latest document draft of any status; null: the run never had one. */
  document_draft?: { id: string; document_id: string | null; status: 'open' | 'promoted' | 'discarded' } | null;
  inputs: { seq: number; text: string; status: 'queued' | 'delivered' | 'discarded'; turn: number | null; created_at: string }[];
}
export type SessionState = Pick<SessionDetail, 'status' | 'status_message' | 'turns' | 'input_tokens' | 'output_tokens' | 'cached_tokens'
  | 'draft' | 'draft_turn' | 'document_id' | 'document_draft' | 'worker_active' | 'queued' | 'model_calls' | 'reasoning_tokens'>;
export interface SessionEvent { id: number; turn: number; seq: number; kind: string; payload: Record<string, unknown>; created_at: string }

const session = (id: string) => `/api/sessions/${encodeURIComponent(id)}`;
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

const query = (values: Record<string, string | number | undefined>) => {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) if (value !== undefined && value !== '') params.set(key, String(value));
  const text = params.toString().replace(/\+/g, '%20');
  return text ? `?${text}` : '';
};
export const getSessions = (filter: { agentId?: string; purpose?: SessionPurpose; documentId?: string; serverId?: string; offset?: number }, signal?: AbortSignal) =>
  request<{ sessions: RunSummary[] }>(`/api/sessions${query({ agent_id: filter.agentId, document_id: filter.documentId, server_id: filter.serverId, purpose: filter.purpose, offset: filter.offset })}`, { signal });
export const getSessionOptions = (scope: { documentId?: string; agentId?: string; serverId?: string; newDocument?: boolean }, signal?: AbortSignal) =>
  request<AgentOptions>(`/api/sessions/options${query({ document_id: scope.documentId, agent_id: scope.agentId, server_id: scope.serverId,
    new_document: scope.newDocument ? 1 : undefined })}`, { signal });
export const reviewSession = (id: string, comment: string, turn?: number) =>
  request<SessionReview>(`${session(id)}/review`, json('POST', { comment, ...(turn ? { turn } : {}) }));
export const previewSession = (value: StartInput) =>
  request<{ config: Record<string, unknown>; first_message: string }>('/api/sessions/preview', json('POST', value));
export const startSession = (value: StartInput) => request<SessionDetail>('/api/sessions', json('POST', value));
export const sessionPath = (id: string) => `/sessions/${encodeURIComponent(id)}`;


export interface AgentResource { id: string; name: string; linked: boolean }
export interface AgentServer extends AgentResource { usable: boolean; reason: string | null }
/** A service or environment: whether it brings a code project, and a server the run can use. */
export interface AgentBringer extends AgentResource { brings_code: boolean; brings_servers: boolean }
export interface AgentTool { name: string; label: string; summary: string; group: string; needs: string | null; requires: string | null }
export interface AgentOptions {
  /** Set when the options are for a saved agent. */
  agent?: { id: string; name: string; revision: number; model: { id: string; name: string } | null; system_prompt: string;
    resources_fixed: boolean; subagents_allowed: boolean };
  models: { id: string; name: string }[]; default_model_id: string | null;
  prompts: { id: string; name: string; content: string }[]; default_prompt_id: string | null;
  resources: { servers: AgentServer[]; services: AgentBringer[]; environments: AgentBringer[]; code_projects: AgentResource[]; collections: AgentResource[];
    documents: { id: string; name: string; linked: boolean; editable: boolean }[] };
  tools: AgentTool[]; experts: { id: string; name: string; when_to_use: string }[];
}
export interface AgentResources { server_ids: string[]; service_ids: string[]; environment_ids: string[]; code_project_ids: string[]; collection_ids: string[] }
/** A document a run writes from scratch: its name and documentation folder. */
export interface NewDocument { folder: string; name: string }
export interface StartInput {
  started_from?: { document_id: string } | { new_document: NewDocument } | { server_id: string }; agent_id?: string; message: string; model_id?: string;
  prompt_id?: string | null; system_prompt?: string; context: string;
  resources: AgentResources & { documents: ({ id: string; editable: boolean } | { new: NewDocument; editable: true })[] };
  tools: string[]; expert_ids: string[]; subagents: boolean;
}
/** A server's discovery runs also carry their proposal counts. */
export type RunSummary = SessionSummary & { started_by: string | null; draft_id: string | null; pending?: number; accepted?: number; rejected?: number };
export const agentRunPath = (documentId: string, runId: string) =>
  `/documents/${encodeURIComponent(documentId)}/agent/${encodeURIComponent(runId)}`;

/** Which tools apply: a tool shows when what it needs is selected (inventory and other no-need tools always). A ticked
 * service or environment counts only for what it brings (a code project; a server the run can use), as Start judges it. */
export function toolApplies(tool: AgentTool, chosen: AgentResources, resources: AgentOptions['resources']): boolean {
  const ticked = [...resources.services.filter(item => chosen.service_ids.includes(item.id)),
    ...resources.environments.filter(item => chosen.environment_ids.includes(item.id))];
  const code = chosen.code_project_ids.length > 0 || ticked.some(item => item.brings_code);
  switch (tool.needs) {
    case null: return true;
    case 'ssh': return resources.servers.some(item => item.usable && chosen.server_ids.includes(item.id)) || ticked.some(item => item.brings_servers);
    case 'editable_document': return true;  // the Ask agent always gives one
    case 'started_from_server': return true;  // the backend offers these only for a server scope
    case 'documents': return chosen.collection_ids.length > 0;
    case 'files': return code || chosen.collection_ids.length > 0;  // matches `requires`: a code project or a collection
    default: return code;  // code, code_graph
  }
}

const SAVE_TOOLS = ['save_document', 'edit_document', 'save_doc', 'edit_doc'];
/** Whether the turn saved the document's draft: a save or edit call that did not fail. */
export const savedIn = (turn: Turn) => turn.work.some(item => item.kind === 'tool' && SAVE_TOOLS.includes(item.name ?? '')
  && !item.isError && (item.preview ?? '').startsWith('Saved draft'));

