import { request } from './api';

export interface ToolCount { name: string; calls: number }
/** "code_graph 12 · read_file 31", or "No tools used". */
export const toolUsageLine = (counts?: ToolCount[] | null) =>
  counts?.length ? counts.map(({ name, calls }) => `${name} ${calls}`).join(' · ') : 'No tools used';
/** The most used tools and how many more: "read_file 31 · code_graph 12 · shell_run 4 +1". */
export function topTools(counts?: ToolCount[] | null, limit = 3) {
  if (!counts?.length) return 'No tools used';
  const more = counts.length - limit;
  return toolUsageLine(counts.slice(0, limit)) + (more > 0 ? ` +${more}` : '');
}

export interface RunSummary {
  run_key: string; agent_id: string; agent: string | null; kind: string | null; title: string | null;
  user: string | null; status: string | null; total_tokens: number; started_at: string; tool_counts: ToolCount[];
}
export interface RunEvent { seq: number; kind: string; agent_id: string; payload: Record<string, unknown>; created_at: string }
export interface RunRecord { run_key: string; agent_id: string; user: string | null; events: RunEvent[]; tool_counts?: ToolCount[] }
export interface RunFilters { kind?: string; agent_id?: string; user?: string; key_prefix?: string; before?: number }

export const runPath = (runKey: string) => `/activity/runs/${encodeURIComponent(runKey)}`;

export function getRuns(filters: RunFilters, signal?: AbortSignal) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) if (value !== undefined && value !== '') params.set(key, String(value));
  const query = params.toString();
  return request<{ runs: RunSummary[]; next: number | null }>(`/api/agent-runs${query ? `?${query}` : ''}`, { signal });
}

export const getRunRecord = (runKey: string, signal?: AbortSignal) =>
  request<RunRecord>(`/api/agent-runs/${encodeURIComponent(runKey)}`, { signal });
