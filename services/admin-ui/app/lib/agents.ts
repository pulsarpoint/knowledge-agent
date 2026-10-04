import type { AgentContext, ContextOptions, ChatEvent, Transcript } from "@knowledge-agent/chat-ui/types";
export interface AgentStatus {
  name: string; ask_enabled: boolean; ask_reason: string | null; model: string | null;
  sources: { docs: boolean };
}
const base = "/api/agents/search";
async function request<T>(path: string, agentId = "search"): Promise<T> {
  const response = await fetch(base + path + `?agent_id=${encodeURIComponent(agentId)}`);
  const body = await response.json();
  if (!response.ok) throw new Error(body?.error?.message ?? "Agent service unavailable");
  return body as T;
}
export const getAgentStatus = (agentId = "search") => request<AgentStatus>("/status", agentId);
export const getContext = (_app: string, agentId = "search") => request<ContextOptions>("/context", agentId);
export const getChat = (_app: string, key: string, agentId = "search") => request<Transcript>(`/chats/${encodeURIComponent(key)}`, agentId);
export async function* streamChat(_app: string, question: string, session?: string, sources?: Record<string, boolean>, context?: AgentContext, agentId = "search"): AsyncGenerator<ChatEvent> {
  const response = await fetch(base + `/chat?agent_id=${encodeURIComponent(agentId)}`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, session, sources, context }),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.error?.message ?? `Request failed (${response.status})`);
  }
  yield { type: "todo", text: await response.text() };
}

export function forAgent(agentId: string) {
  return {
    getContext: (app: string) => getContext(app, agentId),
    getChat: (app: string, key: string) => getChat(app, key, agentId),
    streamChat: (app: string, question: string, session?: string, sources?: Record<string, boolean>, context?: AgentContext) => streamChat(app, question, session, sources, context, agentId),
  };
}
