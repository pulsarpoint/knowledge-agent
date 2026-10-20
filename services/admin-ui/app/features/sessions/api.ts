import { request } from "~/lib/api";
import type { ToolCount } from "./runs";
import type { AgentKey, ResourceIds } from "~/lib/resources";
import type { Turn } from "./turns";
import type { Proposal } from "~/features/servers/discovery";

export const formatTokens = (value: number) => value.toLocaleString("en-US");
const json = (method: string, value: unknown) => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(value),
});

export type SessionPurpose = "documents" | "test" | "event" | "resource" | "revision";
export type SessionStatus = "starting" | "working" | "waiting" | "stopped" | "failed" | "closed";
export const statusLabels: Record<SessionStatus, string> = {
  starting: "Starting",
  working: "Working",
  waiting: "Waiting for you",
  stopped: "Stopped",
  failed: "Failed",
  closed: "Finished",
};
/** A tuning suggestion; `response_turn` is the answer the feedback was about (none: the whole session). */
export interface SessionReview {
  id: string;
  comment: string;
  suggestion: string;
  response_turn?: number | null;
  created_at: string;
  changes?: unknown;
  memory_notes?: { name: string }[] | null;
}
/** A server, service or environment: the resources whose pages have chats. */
export type ResourceType = "server" | "service" | "environment";
export const RESOURCE_TYPES: ResourceType[] = ["server", "service", "environment"];
/** The tag of a resource's chats: servers/<id>, services/<id>, environments/<id>. */
export const resourceTag = (resource: { type: ResourceType; id: string }) => `${resource.type}s/${resource.id}`;
/** The resource a run's tags name (its first resource tag), or null. */
export function resourceOf(tags: readonly string[] | undefined): { type: ResourceType; id: string } | null {
  for (const tag of tags ?? []) {
    const [plural, id, ...rest] = tag.split("/");
    const type = RESOURCE_TYPES.find((item) => `${item}s` === plural);
    if (type && id && rest.length === 0) return { type, id };
  }
  return null;
}
/** A resource's page, with one of its chats open (`?chat=`) or none. */
export const resourceChatPath = (resource: { type: ResourceType; id: string }, chatId?: string | null) =>
  `/${resource.type}s/${encodeURIComponent(resource.id)}${chatId ? `?chat=${encodeURIComponent(chatId)}` : ""}`;

/** What a chat is about: a document (Ask agent), a saved agent (its Test tab), a new document (Documents → New: the
 * Title and Folder the editor holds now; `inventoryFor`: from New inventory document, the resource it is built for) or a
 * server, service or environment (its page's chats). */
export type ChatScope =
  | { kind: "document"; id: string; title: string }
  | { kind: "agent"; id: string; name: string }
  | { kind: "new-document"; folder: string; title: string; inventoryFor?: { type: ResourceType; id: string } }
  | { kind: ResourceType; id: string; name: string };
export type ResourceScope = Extract<ChatScope, { kind: ResourceType }>;
export const isResourceScope = (scope: ChatScope): scope is ResourceScope =>
  (RESOURCE_TYPES as string[]).includes(scope.kind);

/** One resource a run was given (its access entry with source 'given'). */
export interface GivenResource {
  type: string;
  id: string;
}
export interface SessionSummary {
  id: string;
  title: string;
  agent_id: string | null;
  agent_name: string;
  model_name: string;
  status: SessionStatus;
  status_message: string;
  turns: number;
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  purpose: SessionPurpose;
  /** A resource's chats carry its tag (servers/<id>, …); wizard runs also `proposals`. */
  tags?: string[];
  model_calls: number;
  reasoning_tokens: number;
  document_id: string | null;
  document_title: string | null;
  folder: string | null; // null for a Test session
  worker_active: boolean;
  queued: number;
  created_at: string;
  last_activity_at: string;
  closed_at: string | null;
}
export interface SessionDetail extends SessionSummary {
  /** Ask agent runs: the document they revise. */
  target_document_id?: string | null;
  tool_counts?: ToolCount[];
  /** The id of the last tool_call event the counts include (0: none). */
  tool_counts_through_id?: number;
  server_id?: string | null;
  document_name: string | null;
  draft: string | null;
  draft_turn: number | null;
  model_id: string;
  /** What the session started with: a saved agent's run its agent's system prompt and context; an ad-hoc run the
   *  user's system prompt, its context and the saved prompt chosen (null: their own). */
  prompt_snapshot: {
    system_prompt: string;
    context?: string;
    prompt_id?: string | null;
  };
  /** Where the run started: `{server_id | service_id | environment_id}` (a chat on a resource), `{new_document}`
   *  (Documents → New), `{document_id}` (Ask agent); `{}` for a saved agent's run. */
  context_snapshot: Record<string, unknown>;
  /** What the run was given: what the person chose, an environment rather than what it brings (Start new chat with
   *  these settings restores it). */
  given_resources: GivenResource[];
  /** Test sessions: the configuration pinned when the session started (credential references only). */
  config?: Record<string, unknown>;
  /** Test sessions: their saved suggestions, oldest first. */
  reviews?: SessionReview[];
  /** The run's last document draft: open while it exists, else what became of it (promoted, discarded, or replaced:
   *  another draft of its document was promoted and deleted it); null: the run never had one. */
  document_draft?: {
    id: string;
    document_id: string | null;
    status: "open" | "promoted" | "discarded" | "replaced";
  } | null;
  inputs: {
    seq: number;
    text: string;
    status: "queued" | "delivered" | "discarded";
    turn: number | null;
    created_at: string;
  }[];
}
export type SessionState = Pick<
  SessionDetail,
  | "status"
  | "status_message"
  | "turns"
  | "input_tokens"
  | "output_tokens"
  | "cached_tokens"
  | "draft"
  | "draft_turn"
  | "document_id"
  | "document_draft"
  | "worker_active"
  | "queued"
  | "model_calls"
  | "reasoning_tokens"
>;
export interface SessionEvent {
  id: number;
  turn: number;
  seq: number;
  kind: string;
  payload: Record<string, unknown>;
  created_at: string;
}

const session = (id: string) => `/api/sessions/${encodeURIComponent(id)}`;
export const getSession = (id: string, signal?: AbortSignal) => request<SessionDetail>(session(id), { signal });
/** Events per page; a session page opens with the latest page and loads earlier pages as you scroll up. */
export const HISTORY_PAGE = 100;
/** The live stream after an event id (0: from the beginning); on a reconnect the browser resumes from the last id. */
export const sessionEventsUrl = (id: string, after = 0) => `${session(id)}/events?after=${after}`;
export const getSessionHistory = (id: string, before?: number, signal?: AbortSignal) =>
  request<{ events: SessionEvent[]; has_more: boolean }>(
    `${session(id)}/events/history?${before ? `before=${before}&` : ""}limit=${HISTORY_PAGE}`,
    { signal },
  );
export const sendMessage = (id: string, text: string) =>
  request<{ seq: number; status: "queued"; working: boolean }>(`${session(id)}/messages`, json("POST", { text }));
export const stopSession = (id: string) =>
  request<{ id: string; discarded: number }>(`${session(id)}/stop`, { method: "POST" });
export const closeSession = (id: string) => request<{ id: string }>(`${session(id)}/close`, { method: "POST" });

const query = (values: Record<string, string | number | undefined>) => {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values))
    if (value !== undefined && value !== "") params.set(key, String(value));
  const text = params.toString().replace(/\+/g, "%20");
  return text ? `?${text}` : "";
};
export const getSessions = (
  filter: { agentId?: string; purpose?: SessionPurpose; documentId?: string; tag?: string; offset?: number },
  signal?: AbortSignal,
) =>
  request<{ sessions: RunSummary[] }>(
    `/api/sessions${query({ agent_id: filter.agentId, document_id: filter.documentId, tag: filter.tag, purpose: filter.purpose, offset: filter.offset })}`,
    { signal },
  );
export const getSessionOptions = (
  scope: {
    documentId?: string;
    agentId?: string;
    serverId?: string;
    serviceId?: string;
    environmentId?: string;
    newDocument?: boolean;
  },
  signal?: AbortSignal,
) =>
  request<AgentOptions>(
    `/api/sessions/options${query({
      document_id: scope.documentId,
      agent_id: scope.agentId,
      server_id: scope.serverId,
      service_id: scope.serviceId,
      environment_id: scope.environmentId,
      new_document: scope.newDocument ? 1 : undefined,
    })}`,
    { signal },
  );
export const reviewSession = (id: string, comment: string, turn?: number) =>
  request<SessionReview>(`${session(id)}/review`, json("POST", { comment, ...(turn ? { turn } : {}) }));
/** One resource of a run's access object. */
export interface AccessResource {
  type: string;
  id: string;
  name: string;
  description_short: string;
  source: string;
  editable?: boolean;
}
/** A run's access object: the resources it may use and its roles (fixed at start). */
export interface RunAccess {
  resources: AccessResource[];
  roles: { content_editor: boolean };
}
/** What a run's model receives (its Debug tab): the assembled instructions, where the prompt came from, the tools as the
 * model sees them, and what the run may use (its access object). Secrets are redacted. */
export interface SessionDebug {
  instructions: string;
  /** The library prompt and its version (null: the text was edited before the start); null: no library prompt. */
  prompt: { id: string; name: string; version: number | null } | null;
  /** `wizard`: the prompt was written in the Propose wizard. */
  prompt_source: "library" | "wizard";
  agent_instructions: string;
  skills: { name: string }[];
  tools: { name: string; description: string }[];
  /** What the run may use: each resource with its source (`given`, or `via <type> <id>` of the resource it came with) and
   * the roles. */
  access: RunAccess;
  model: { id: string; name: string } | null;
  /** What the start left out, and why (an inventory document's server without usable SSH). */
  notes?: string[];
}
export const getSessionDebug = (id: string, signal?: AbortSignal) =>
  request<SessionDebug>(`${session(id)}/debug`, { signal });
/** The configuration a start would pin, and its Debug view (built like a run's). */
export const previewSession = (value: StartInput, signal?: AbortSignal) =>
  request<{ config: Record<string, unknown>; first_message: string } & SessionDebug>("/api/sessions/preview", {
    ...json("POST", value),
    signal,
  });
/** A run's proposals on a resource's Proposals page. */
export interface ProposalRun {
  session_id: string;
  title: string;
  started_at: string;
  started_by: string | null;
  proposals: Proposal[];
}
/** Every proposal made in the runs tagged with a resource, grouped by run (newest first), of one status or all. */
export const getResourceProposals = (
  resource: { type: ResourceType; id: string },
  status?: Proposal["status"],
  signal?: AbortSignal,
) =>
  request<{ runs: ProposalRun[] }>(`/api/discovery/proposals${query({ tag: resourceTag(resource), status })}`, {
    signal,
  });
export const startSession = (value: StartInput) => request<SessionDetail>("/api/sessions", json("POST", value));
export const sessionPath = (id: string) => `/sessions/${encodeURIComponent(id)}`;

export interface AgentResource {
  id: string;
  name: string;
  linked: boolean;
}
export interface AgentServer extends AgentResource {
  usable: boolean;
  reason: string | null;
}
/** A service or environment: whether it brings a code project, and a server the run can use. */
export interface AgentBringer extends AgentResource {
  brings_code: boolean;
  brings_servers: boolean;
}
export interface AgentOptions {
  /** Set when the options are for a saved agent. */
  agent?: {
    id: string;
    name: string;
    revision: number;
    model: { id: string; name: string } | null;
    system_prompt: string;
  };
  models: { id: string; name: string }[];
  default_model_id: string | null;
  prompts: { id: string; name: string; content: string }[];
  default_prompt_id: string | null;
  resources: {
    servers: AgentServer[];
    services: AgentBringer[];
    environments: AgentBringer[];
    code_projects: AgentResource[];
    collections: AgentResource[];
    documents: { id: string; name: string; linked: boolean; editable: boolean }[];
  };
  experts: { id: string; name: string; when_to_use: string }[];
}
export type AgentResources = ResourceIds<AgentKey>;
/** A document a run writes from scratch: its name, documentation folder and tags; tagged `inventory` with one resource
 * id, an inventory document of that server, service or environment (built for it alone). */
export interface NewDocument {
  folder: string;
  name: string;
  tags?: string[];
  server_id?: string;
  service_id?: string;
  environment_id?: string;
}
export interface StartInput {
  started_from?:
    | { document_id: string }
    | { new_document: NewDocument }
    | { server_id: string }
    | { service_id: string }
    | { environment_id: string };
  /** Tags the run gets besides its resource's (the backend adds that one), e.g. `proposals`. */
  tags?: string[];
  agent_id?: string;
  message: string;
  model_id?: string;
  prompt_id?: string | null;
  system_prompt?: string;
  context: string;
  resources: AgentResources & {
    documents: ({ id: string; editable: boolean } | { new: NewDocument; editable: true })[];
  };
  expert_ids: string[];
  /** An ad-hoc run that may change content asks for the content editor role (the Propose wizard). */
  content_editor?: boolean;
}
/** A resource's chats (listed by tag) also carry their proposal counts. */
export type RunSummary = SessionSummary & {
  started_by: string | null;
  draft_id: string | null;
  pending?: number;
  accepted?: number;
  rejected?: number;
};
export const agentRunPath = (documentId: string, runId: string) =>
  `/documents/${encodeURIComponent(documentId)}/agent/${encodeURIComponent(runId)}`;

const SAVE_TOOLS = ["save_document", "edit_document", "save_doc", "edit_doc"];
/** Whether the turn saved the document's draft: a save or edit call that did not fail. */
export const savedIn = (turn: Turn) =>
  turn.work.some(
    (item) =>
      item.kind === "tool" &&
      SAVE_TOOLS.includes(item.name ?? "") &&
      !item.isError &&
      (item.preview ?? "").startsWith("Saved draft"),
  );
