import { request } from './api';
export interface CodeStatus { repositories: { name: string; label: string; path_exists: boolean }[]; model: string | null; ask_enabled: boolean; ask_reason: string | null }
export interface CodeCitation { file: string; lines: string; quote: string }
export interface CodeEvent { type: string; text?: string; answer?: string; message?: string; citations?: CodeCitation[]; not_found?: boolean }
export const getCodeStatus = (agentId = "code") => request<CodeStatus>(`/api/agents/code/status?agent_id=${encodeURIComponent(agentId)}`);
export async function* askCode(repository: string, question: string, context: string, signal: AbortSignal, agentId = "code"): AsyncGenerator<CodeEvent> {
  const response = await fetch(`/api/agents/code/ask?agent_id=${encodeURIComponent(agentId)}`, { method: 'POST', signal,
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ repository, question, context: context.trim() || null }) });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.error?.message ?? 'Code search is unavailable.');
  }
  yield { type: "todo", text: await response.text() };
}
