import { request } from "./api";

export type EventStatus = "pending" | "processed" | "failed";
export interface EventRow { id: number; source: string; type: string; subject: string; title: string; occurred_at: string; created_at: string;
  status: EventStatus; depth: number; parent_id: number | null; root_id: number; handler_id: string | null }
export interface HandlerRun { handler_id: string; handler_name: string; status: "succeeded" | "failed"; message: string;
  emitted_event_id: number | null; agent_session_id: string | null; agent_id: string | null; created_at: string }
export interface EventDetail extends EventRow { source_id: string | null; dedupe_key: string; payload: Record<string, unknown>;
  parent: EventRow | null; chain: EventRow[]; runs: HandlerRun[] }
export interface EventFilters { source: string; type: string; status: string; handler: string; q: string }
export type SortKey = "time" | "source" | "type" | "subject" | "status";
export interface EventSort { key: SortKey; order: "asc" | "desc" }
export interface EventFacets { sources: string[]; types: string[]; statuses: EventStatus[]; handlers: { id: string; name: string }[] }
export type FieldChange = { from: unknown; to: unknown } | { added: string[]; removed: string[] } | { changed: true };

export const DEFAULT_SORT: EventSort = { key: "time", order: "desc" };
export const NO_FILTERS: EventFilters = { source: "", type: "", status: "", handler: "", q: "" };
export const TYPE_LABELS: Record<string, string> = { "jira.ticket.created": "Ticket created", "jira.ticket.updated": "Ticket updated" };
export const SOURCE_LABELS: Record<string, string> = { jira: "Jira", handler: "Handler" };
export const STATUS_LABELS: Record<EventStatus, string> = { pending: "Pending", processed: "Processed", failed: "Failed" };
const FIELD_LABELS: Record<string, string> = { summary: "Summary", status: "Status", issue_type: "Type", priority: "Priority", assignee: "Assignee", labels: "Labels", description: "Description" };

export function getEvents(filters: EventFilters, sort: EventSort = DEFAULT_SORT, offset = 0, signal?: AbortSignal) {
  const query = new URLSearchParams();
  if (filters.source) query.set("source", filters.source);
  if (filters.type) query.set("type", filters.type);
  if (filters.status) query.set("status", filters.status);
  if (filters.handler) query.set("handler_id", filters.handler);
  if (filters.q.trim()) query.set("q", filters.q.trim());
  if (sort.key !== DEFAULT_SORT.key) query.set("sort", sort.key);
  if (sort.key !== DEFAULT_SORT.key || sort.order !== DEFAULT_SORT.order) query.set("order", sort.order);
  if (offset > 0) query.set("offset", String(offset));
  const suffix = query.toString();
  return request<{ events: EventRow[]; next_offset: number | null }>(`/api/events${suffix ? `?${suffix}` : ""}`, { signal });
}
export const getEventFacets = (signal?: AbortSignal) => request<EventFacets>("/api/events/facets", { signal });
export const getEvent = (id: number, signal?: AbortSignal) => request<{ event: EventDetail }>(`/api/events/${id}`, { signal });

/** One readable line per change, e.g. "Status: Open → In Progress", "Labels: +agent −old", "Description changed". */
export function describeChanges(changes: unknown): string[] {
  if (!changes || typeof changes !== "object") return [];
  const show = (value: unknown) => value === null || value === undefined || value === "" ? "—" : String(value);
  return Object.entries(changes as Record<string, unknown>).flatMap(([field, value]) => {
    if (!value || typeof value !== "object") return [];
    const change = value as Record<string, unknown>;
    const label = FIELD_LABELS[field] ?? field;
    if ("added" in change || "removed" in change) {
      const added = Array.isArray(change.added) ? (change.added as unknown[]) : [];
      const removed = Array.isArray(change.removed) ? (change.removed as unknown[]) : [];
      if (added.length === 0 && removed.length === 0) return [];
      return [`${label}: ${[...added.map(item => `+${item}`), ...removed.map(item => `−${item}`)].join(" ")}`];
    }
    if ("changed" in change) return [`${label} changed`];
    return [`${label}: ${show(change.from)} → ${show(change.to)}`];
  });
}
