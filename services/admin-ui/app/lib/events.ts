import { request } from "./api";

export interface EventRow { id: number; source: string; type: string; subject: string; title: string; occurred_at: string; created_at: string }
export interface EventDetail extends EventRow { source_id: string | null; dedupe_key: string; payload: Record<string, unknown> }
export interface EventFilters { source: string; type: string; q: string }
export type FieldChange = { from: unknown; to: unknown } | { added: string[]; removed: string[] } | { changed: true };

export const TYPE_LABELS: Record<string, string> = { "jira.ticket.created": "Ticket created", "jira.ticket.updated": "Ticket updated" };
export const SOURCE_LABELS: Record<string, string> = { jira: "Jira" };
const FIELD_LABELS: Record<string, string> = { summary: "Summary", status: "Status", issue_type: "Type", priority: "Priority", assignee: "Assignee", labels: "Labels", description: "Description" };

export function getEvents(filters: EventFilters, before?: number, signal?: AbortSignal) {
  const query = new URLSearchParams();
  if (filters.source) query.set("source", filters.source);
  if (filters.type) query.set("type", filters.type);
  if (filters.q.trim()) query.set("q", filters.q.trim());
  if (before) query.set("before", String(before));
  const suffix = query.toString();
  return request<{ events: EventRow[]; next_before: number | null }>(`/api/events${suffix ? `?${suffix}` : ""}`, { signal });
}
export const getEventFacets = (signal?: AbortSignal) => request<{ sources: string[]; types: string[] }>("/api/events/facets", { signal });
export const getEvent = (id: number, signal?: AbortSignal) => request<{ event: EventDetail }>(`/api/events/${id}`, { signal });

/** One readable line per change, e.g. "Status: Open → In Progress", "Labels: +agent −old", "Description changed". */
export function describeChanges(changes: unknown): string[] {
  if (!changes || typeof changes !== "object") return [];
  const show = (value: unknown) => value === null || value === undefined || value === "" ? "—" : String(value);
  return Object.entries(changes as Record<string, FieldChange>).map(([field, change]) => {
    const label = FIELD_LABELS[field] ?? field;
    if ("added" in change) return `${label}: ${[...change.added.map(item => `+${item}`), ...change.removed.map(item => `−${item}`)].join(" ")}`;
    if ("changed" in change) return `${label} changed`;
    return `${label}: ${show(change.from)} → ${show(change.to)}`;
  });
}
