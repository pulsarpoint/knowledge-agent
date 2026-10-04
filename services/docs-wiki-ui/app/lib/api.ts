import type { AgentContext, ContextOptions, ChatEvent, ChatSource, Transcript } from "@knowledge-agent/chat-ui/types";
import { parseSse } from "./sse";
export type { ChatEvent, ChatSource, Transcript } from "@knowledge-agent/chat-ui/types";

export interface AppInfo {
  name: string;
  ask_enabled: boolean;
  ask_reason: string | null;
  context_enabled?: boolean;
  // Capability discovery for the chat source selector. Absent on older
  // servers — callers should treat that as "docs only" (no selector).
  sources?: { docs: boolean };
}
export interface TocGroup { group: string; docs: { title: string; path: string; freshness: string }[] }
export interface DocData {
  path: string; title: string; markdown: string;
  front_matter: Record<string, unknown>; freshness: string;
  headings: { text: string; anchor: string; level: number }[];
}
export interface SearchHit { path: string; title: string; heading_path: string; snippet: string; anchor: string | null }
// Code answers recorded in the gaps log by the former "Ask the code" escalation.
export interface CodeCitation { file: string; lines: string; quote?: string }
export interface CodeAnswer { answer: string; citations: CodeCitation[]; not_found: boolean; timestamp: string }
export interface GapsData {
  gaps: {
    question: string; timestamp: string;
    near_misses: { path: string; heading_path: string }[];
    code_answer?: CodeAnswer;
  }[];
  skipped: number;
}

export interface AuditSession {
  session_key: string; first_question: string; questions: number; first_ts: string; last_ts: string;
}
export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = init ? await fetch(url, init) : await fetch(url);
  if (!response.ok) {
    let message = `request failed (${response.status})`;
    try {
      const body = await response.json();
      if (body?.error?.message) message = body.error.message;
    } catch { /* non-JSON error body — keep the status message */ }
    throw new ApiError(message, response.status);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const getApps = () => request<AppInfo[]>("/api/apps");
export const getContext = (app: string) => request<ContextOptions>(`/api/${encodeURIComponent(app)}/context`);
export const getToc = (app: string) => request<TocGroup[]>(`/api/${encodeURIComponent(app)}/toc`);
export const getDoc = (app: string, path: string) =>
  request<DocData>(`/api/${encodeURIComponent(app)}/doc?path=${encodeURIComponent(path)}`);
export const search = (app: string, q: string) =>
  request<SearchHit[]>(`/api/${encodeURIComponent(app)}/search?q=${encodeURIComponent(q)}`);
export const getGaps = (app: string) => request<GapsData>(`/api/${encodeURIComponent(app)}/gaps`);
export const getChat = (app: string, key: string) =>
  request<Transcript>(`/api/${encodeURIComponent(app)}/chats/${encodeURIComponent(key)}`);
export const deleteChat = (app: string, key: string) =>
  request<void>(`/api/${encodeURIComponent(app)}/chats/${encodeURIComponent(key)}`, { method: "DELETE" });
export const getMyChats = (app: string) =>
  request<{ sessions: AuditSession[] }>(`/api/${encodeURIComponent(app)}/chats`);

export async function* streamChat(
  app: string, question: string, session?: string, sources?: Record<string, boolean>,
  context?: AgentContext,
): AsyncGenerator<ChatEvent> {
  let response: Response;
  try {
    const body: Record<string, unknown> = { question };
    if (session) body.session = session;
    if (sources) body.sources = sources;
    if (context) body.context = context;
    response = await fetch(`/api/${encodeURIComponent(app)}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    yield { type: "error", message: "network error — is the server running?" };
    return;
  }
  if (!response.ok) {
    let message = `request failed (${response.status})`;
    try {
      const body = await response.json();
      if (body?.error?.message) message = body.error.message;
    } catch { /* keep status message */ }
    yield { type: "error", message };
    return;
  }
  if (!response.body) { yield { type: "error", message: "Chat is temporarily unavailable." }; return; }
  let finished = false;
  for await (const { event, data } of parseSse(response.body)) {
    const payload = (data ?? {}) as Record<string, unknown>;
    if (event === "done" || event === "error") finished = true;
    yield { type: event, ...payload } as ChatEvent;
    if (finished) return;
  }
  if (!finished) yield { type: "error", message: "Chat is temporarily unavailable." };
}
