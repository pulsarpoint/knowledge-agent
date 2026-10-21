import { request } from "~/lib/api";
import type { SessionStatus } from "~/features/sessions/api";

export type EventStatus = "pending" | "processed" | "failed";
export interface EventRow {
  id: number;
  source: string;
  type: string;
  subject: string;
  title: string;
  occurred_at: string;
  created_at: string;
  status: EventStatus;
  /** How many handlers ran on the event (0 when none matched). */
  handlers?: number;
  depth: number;
  parent_id: number | null;
  root_id: number;
  handler_id: string | null;
}
export type TaskStatus = "queued" | "running" | "succeeded" | "failed";
export interface AgentTaskRef {
  id: string;
  status: TaskStatus;
  attempts: number;
  notification_id: number | null;
}
export interface RunRef {
  id: string;
  status: SessionStatus;
  agent_id: string;
  notification_id: number | null;
}
export interface HandlerRun {
  handler_id: string;
  handler_name: string;
  status: "succeeded" | "failed";
  message: string;
  emitted_event_id: number | null;
  agent_session_id: string | null;
  agent_id: string | null;
  created_at: string;
  run?: RunRef | null;
  agent_task: AgentTaskRef | null;
}
export interface EventDetail extends EventRow {
  source_id: string | null;
  dedupe_key: string;
  created_manually: boolean;
  payload: Record<string, unknown>;
  parent: EventRow | null;
  chain: EventRow[];
  runs: HandlerRun[];
}
export interface EventFilters {
  source: string;
  type: string;
  status: string;
  /** "yes" (a handler ran) or "no" (none did); empty for all. */
  handled: string;
  handler: string;
  /** An Emit event schedule's name: its events (source schedule, subject the name). */
  schedule: string;
  q: string;
}
export type SortKey = "time" | "source" | "type" | "subject" | "status";
export interface EventSort {
  key: SortKey;
  order: "asc" | "desc";
}
export interface EventFacets {
  sources: string[];
  types: string[];
  statuses: EventStatus[];
  handlers: { id: string; name: string }[];
  /** Emit event schedules by name. */
  schedules: string[];
}
export type FieldChange = { from: unknown; to: unknown } | { added: string[]; removed: string[] } | { changed: true };

export const DEFAULT_SORT: EventSort = { key: "time", order: "desc" };
export const NO_FILTERS: EventFilters = {
  source: "",
  type: "",
  status: "",
  handled: "",
  handler: "",
  schedule: "",
  q: "",
};
export const TYPE_LABELS: Record<string, string> = {
  "jira.ticket.created": "Ticket created",
  "jira.ticket.updated": "Ticket updated",
  "schedule.fired": "Schedule fired",
  "memory.saved": "Memory saved",
  "memory.updated": "Memory updated",
  "memory.restored": "Memory restored",
  "memory.deleted": "Memory deleted",
};
const GROUP_LABELS: Record<string, string> = { "jira.ticket": "Jira ticket", memory: "memory", schedule: "schedule" };
/** The "All … events" patterns for the types: one prefix.* per type prefix (everything but the last part), sorted. */
export function typeGroups(types: string[]): { pattern: string; label: string }[] {
  const prefixes = new Set(
    types.filter((type) => type.includes(".")).map((type) => type.slice(0, type.lastIndexOf("."))),
  );
  return [...prefixes].sort().map((prefix) => ({
    pattern: `${prefix}.*`,
    label: `All ${GROUP_LABELS[prefix] ?? `${prefix}.*`} events`,
  }));
}
export const SOURCE_LABELS: Record<string, string> = {
  jira: "Jira",
  handler: "Handler",
  manual: "Manual",
  memory: "Memory",
  schedule: "Schedule",
};
export const TASK_STATUS_LABELS: Record<TaskStatus, string> = {
  queued: "Queued",
  running: "Running",
  succeeded: "Succeeded",
  failed: "Failed",
};
export const STATUS_LABELS: Record<EventStatus, string> = {
  pending: "Pending",
  processed: "Processed",
  failed: "Failed",
};
const FIELD_LABELS: Record<string, string> = {
  summary: "Summary",
  status: "Status",
  issue_type: "Type",
  priority: "Priority",
  assignee: "Assignee",
  labels: "Labels",
  description: "Description",
};

export function getEvents(filters: EventFilters, sort: EventSort = DEFAULT_SORT, offset = 0, signal?: AbortSignal) {
  const query = new URLSearchParams();
  if (filters.source || filters.schedule) query.set("source", filters.schedule ? "schedule" : filters.source);
  if (filters.type) query.set("type", filters.type);
  if (filters.status) query.set("status", filters.status);
  if (filters.handled) query.set("handled", filters.handled);
  if (filters.handler) query.set("handler_id", filters.handler);
  if (filters.schedule) query.set("subject", filters.schedule);
  if (filters.q.trim()) query.set("q", filters.q.trim());
  if (sort.key !== DEFAULT_SORT.key) query.set("sort", sort.key);
  if (sort.key !== DEFAULT_SORT.key || sort.order !== DEFAULT_SORT.order) query.set("order", sort.order);
  if (offset > 0) query.set("offset", String(offset));
  const suffix = query.toString();
  return request<{ events: EventRow[]; next_offset: number | null }>(`/api/events${suffix ? `?${suffix}` : ""}`, {
    signal,
  });
}
export interface NewEventInput {
  source: "manual" | "jira";
  type: string;
  subject: string;
  title: string;
  payload: Record<string, unknown>;
}
export const createEvent = (input: NewEventInput) =>
  request<{ event: EventDetail }>("/api/events", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
export const getEventFacets = (signal?: AbortSignal) => request<EventFacets>("/api/events/facets", { signal });
export const getEvent = (id: number, signal?: AbortSignal) =>
  request<{ event: EventDetail }>(`/api/events/${id}`, { signal });

/** One readable line per change, e.g. "Status: Open → In Progress", "Labels: +agent −old", "Description changed". */
export function describeChanges(changes: unknown): string[] {
  if (!changes || typeof changes !== "object") return [];
  const show = (value: unknown) => (value === null || value === undefined || value === "" ? "—" : String(value));
  return Object.entries(changes as Record<string, unknown>).flatMap(([field, value]) => {
    if (!value || typeof value !== "object") return [];
    const change = value as Record<string, unknown>;
    const label = FIELD_LABELS[field] ?? field;
    if ("added" in change || "removed" in change) {
      const added = Array.isArray(change.added) ? (change.added as unknown[]) : [];
      const removed = Array.isArray(change.removed) ? (change.removed as unknown[]) : [];
      if (added.length === 0 && removed.length === 0) return [];
      return [`${label}: ${[...added.map((item) => `+${item}`), ...removed.map((item) => `−${item}`)].join(" ")}`];
    }
    if ("changed" in change) return [`${label} changed`];
    return [`${label}: ${show(change.from)} → ${show(change.to)}`];
  });
}
