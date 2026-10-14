import { request } from "~/lib/api";

export type Operator = "equals" | "not_equals" | "contains" | "in" | "matches" | "exists";
export interface Condition { field: string; op: Operator; value: string | string[] | null }
export interface EmitTemplates { type: string; subject: string; title: string; payload: string }
export interface HandlerDraft { name: string; enabled: boolean; type_pattern: string; conditions: Condition[]; action: "emit" | "agent";
  emit: EmitTemplates | null; agent_id: string | null; prompt: string | null }
export interface EventHandler extends HandlerDraft { id: string; agent_name: string | null; revision: number; created_at: string; updated_at: string;
  last_run: { status: "succeeded" | "failed"; created_at: string } | null }
export type PreviewOutput = { type: string; subject: string; title: string; payload: Record<string, unknown> } | { prompt: string } | { error: string };
export interface PreviewResult { matches: boolean; reason: string | null; output: PreviewOutput }

export const OPERATOR_LABELS: Record<Operator, string> = { equals: "equals", not_equals: "does not equal", contains: "contains",
  in: "is one of", matches: "matches regex", exists: "exists" };
export const FIELD_SUGGESTIONS = ["source", "type", "subject", "title", "payload.status", "payload.labels", "payload.assignee",
  "payload.priority", "payload.issue_type", "payload.summary", "payload.changes.status.to", "payload.changes.status.from",
  "payload.changes.assignee.to", "payload.changes.priority.to", "payload.changes.labels.added", "payload.changes.labels.removed"];

const json = (method: string, value: unknown) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) });
export const getHandlers = () => request<{ handlers: EventHandler[] }>("/api/event-handlers");
export const getHandlerAgents = () => request<{ agents: { id: string; name: string }[] }>("/api/event-handlers/agents");
export const createHandler = (draft: HandlerDraft) => request<{ handler: EventHandler }>("/api/event-handlers", json("POST", draft));
export const saveHandler = (id: string, draft: HandlerDraft & { revision: number }) =>
  request<{ handler: EventHandler }>(`/api/event-handlers/${encodeURIComponent(id)}`, json("PUT", draft));
export const deleteHandler = (id: string) => request<void>(`/api/event-handlers/${encodeURIComponent(id)}`, { method: "DELETE" });
export const previewHandler = (draft: HandlerDraft, eventId: number) =>
  request<PreviewResult>("/api/event-handlers/preview", json("POST", { handler: draft, event_id: eventId }));

/** The editable part of a saved handler, as PUT takes it. */
export const toDraft = (handler: EventHandler): HandlerDraft => ({ name: handler.name, enabled: handler.enabled, type_pattern: handler.type_pattern,
  conditions: handler.conditions, action: handler.action, emit: handler.emit, agent_id: handler.agent_id, prompt: handler.prompt });
