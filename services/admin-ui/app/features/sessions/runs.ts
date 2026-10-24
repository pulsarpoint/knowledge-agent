import { request } from "~/lib/api";

export interface ToolCount {
  name: string;
  calls: number;
}
/** "code_graph 12 · read_file 31", or "No tools used". */
export const toolUsageLine = (counts?: ToolCount[] | null) =>
  counts?.length ? counts.map(({ name, calls }) => `${name} ${calls}`).join(" · ") : "No tools used";
/** The most used tools and how many more: "read_file 31 · code_graph 12 · shell_run 4 +1". */
export function topTools(counts?: ToolCount[] | null, limit = 3) {
  if (!counts?.length) return "No tools used";
  const more = counts.length - limit;
  return toolUsageLine(counts.slice(0, limit)) + (more > 0 ? ` +${more}` : "");
}

/** Loaded counts plus the live tool calls after the last one they include (through: that call's event id), most
 * used first, ties by name. Without through nothing is added: the counts may already include the calls. */
export function withLaterCalls(
  counts: ToolCount[],
  through: number | undefined,
  calls: { id: number; name: string }[],
) {
  if (through === undefined) return counts;
  const totals = new Map(counts.map(({ name, calls: n }) => [name, n]));
  for (const call of calls) if (call.id > through) totals.set(call.name, (totals.get(call.name) ?? 0) + 1);
  return [...totals]
    .map(([name, n]) => ({ name, calls: n }))
    .sort((a, b) => b.calls - a.calls || (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
}

export interface RunSummary {
  run_key: string;
  agent_id: string;
  agent: string | null;
  kind: string | null;
  title: string | null;
  user: string | null;
  status: string | null;
  total_tokens: number;
  started_at: string;
  tool_counts: ToolCount[];
  /** expert_call rows: one call of an expert from another agent's run (call_id selects its events). */
  id?: string;
  call_id?: string;
  caller?: string | null;
}
export interface RunEvent {
  seq: number;
  kind: string;
  agent_id: string;
  payload: Record<string, unknown>;
  created_at: string;
}
export interface RunRecord {
  run_key: string;
  agent_id: string;
  user: string | null;
  events: RunEvent[];
  tool_counts?: ToolCount[];
}
/** The listing's URL search params, in request order; each has a default left out of the URL. */
export const RUN_PARAMS = [
  "kind",
  "agent_id",
  "status",
  "has_output",
  "user",
  "key_prefix",
  "sort",
  "order",
  "page",
  "page_size",
];
export const RUN_DEFAULTS: Record<string, string> = { sort: "started", order: "desc", page: "1", page_size: "50" };
export interface RunPage {
  runs: RunSummary[];
  total: number;
  page: number;
  page_size: number;
}

export const runPath = (runKey: string, callId?: string) =>
  `/activity/runs/${encodeURIComponent(runKey)}${callId ? `?call=${encodeURIComponent(callId)}` : ""}`;

/** One page of runs for the listing's search params; empty and default values are not sent. */
export function getRuns(search: URLSearchParams, signal?: AbortSignal) {
  const params = new URLSearchParams();
  for (const key of RUN_PARAMS) {
    const value = search.get(key);
    if (value && value !== RUN_DEFAULTS[key]) params.set(key, value);
  }
  const query = params.toString();
  return request<RunPage>(`/api/agent-runs${query ? `?${query}` : ""}`, { signal });
}

export const getRunRecord = (runKey: string, signal?: AbortSignal, callId?: string) =>
  request<RunRecord>(
    `/api/agent-runs/${encodeURIComponent(runKey)}${callId ? `?call=${encodeURIComponent(callId)}` : ""}`,
    { signal },
  );
