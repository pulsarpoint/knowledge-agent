import { request } from "~/lib/api";

export type Operator = "equals" | "not_equals" | "contains" | "in" | "matches" | "exists";
export interface Condition {
  field: string;
  op: Operator;
  value: string | string[] | null;
}
export interface EmitTemplates {
  type: string;
  subject: string;
  title: string;
  payload: string;
}
export interface HandlerDraft {
  name: string;
  enabled: boolean;
  type_pattern: string;
  conditions: Condition[];
  action: "emit" | "agent";
  emit: EmitTemplates | null;
  /** fixed: agent_id; assignee: the agent mapped to the Jira ticket's assignee (agent_id null). */
  agent_from: "fixed" | "assignee";
  agent_id: string | null;
  prompt: string | null;
  reply_to_jira: boolean;
  /** After the Jira comment is posted, assign the ticket back (previous assignee, else reporter). Only with
   * reply_to_jira and the assignee's agent. */
  give_back: boolean;
}
export interface EventHandler extends HandlerDraft {
  id: string;
  agent_name: string | null;
  revision: number;
  created_at: string;
  updated_at: string;
  last_run: { status: "succeeded" | "failed"; created_at: string } | null;
}
export type PreviewOutput =
  | { type: string; subject: string; title: string; payload: Record<string, unknown> }
  | { prompt: string }
  | { error: string };
export interface PreviewResult {
  matches: boolean;
  reason: string | null;
  output: PreviewOutput;
  /** Send to agent only: the agent that would run, or why none would. */
  target?: { agent_id: string; agent_name: string } | { error: string };
}

export const OPERATOR_LABELS: Record<Operator, string> = {
  equals: "equals",
  not_equals: "does not equal",
  contains: "contains",
  in: "is one of",
  matches: "matches regex",
  exists: "exists",
};
export const FIELD_SUGGESTIONS = [
  "source",
  "type",
  "subject",
  "title",
  "payload.status",
  "payload.labels",
  "payload.assignee",
  "payload.assignee_id",
  "payload.for_assignee",
  "payload.priority",
  "payload.issue_type",
  "payload.summary",
  "payload.changes.status.to",
  "payload.changes.status.from",
  "payload.changes.assignee.to",
  "payload.changes.priority.to",
  "payload.changes.labels.added",
  "payload.changes.labels.removed",
  "payload.fired_at",
  "payload.last_run_at",
  "payload.trigger",
];
/** A schedule's Emit event records this type; a handler runs a schedule through this condition field (equals its id). */
export const SCHEDULE_FIRED = "schedule.fired";
export const SCHEDULE_FIELD = "payload.schedule_id";
/** Every Jira event type the connection records (targets.JIRA_TYPES on the server). */
export const JIRA_TYPES = ["jira.ticket.created", "jira.ticket.updated"];

/** As the server's matching.type_matches: `*` alone matches every type; a `*` part matches one part, a trailing `.*`
 * one or more trailing parts. */
export function typeMatches(pattern: string, type: string): boolean {
  if (pattern === "*") return true;
  const parts = pattern.split(".");
  const expression = parts
    .map((part, index) =>
      part !== "*"
        ? part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
        : index === parts.length - 1
          ? "[a-z0-9_]+(\\.[a-z0-9_]+)*"
          : "[a-z0-9_]+",
    )
    .join("\\.");
  return new RegExp(`^${expression}$`).test(type);
}
/** Agent mapped to the assignee, Reply in Jira and give back work only when the pattern can match a Jira event. */
export const forJira = (pattern: string) => JIRA_TYPES.some((type) => typeMatches(pattern.trim(), type));

/** A handler bound to a schedule: its type takes schedule.fired and it checks payload.schedule_id equals an id. */
export const boundToSchedule = (handler: HandlerDraft) =>
  typeMatches(handler.type_pattern.trim(), SCHEDULE_FIRED) &&
  handler.conditions.some((c) => c.field === SCHEDULE_FIELD && c.op === "equals");

const json = (method: string, value: unknown) => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(value),
});
export const getHandlers = () => request<{ handlers: EventHandler[] }>("/api/event-handlers");
export const getHandlerAgents = () => request<{ agents: { id: string; name: string }[] }>("/api/event-handlers/agents");
export const createHandler = (draft: HandlerDraft) =>
  request<{ handler: EventHandler }>("/api/event-handlers", json("POST", draft));
export const saveHandler = (id: string, draft: HandlerDraft & { revision: number }) =>
  request<{ handler: EventHandler }>(`/api/event-handlers/${encodeURIComponent(id)}`, json("PUT", draft));
export const deleteHandler = (id: string) =>
  request<void>(`/api/event-handlers/${encodeURIComponent(id)}`, { method: "DELETE" });
export const previewHandler = (draft: HandlerDraft, eventId: number) =>
  request<PreviewResult>("/api/event-handlers/preview", json("POST", { handler: draft, event_id: eventId }));

/** The editable part of a saved handler, as PUT takes it. */
export const toDraft = (handler: EventHandler): HandlerDraft => ({
  name: handler.name,
  enabled: handler.enabled,
  type_pattern: handler.type_pattern,
  conditions: handler.conditions,
  action: handler.action,
  emit: handler.emit,
  agent_from: handler.agent_from,
  agent_id: handler.agent_id,
  prompt: handler.prompt,
  reply_to_jira: handler.reply_to_jira,
  give_back: handler.give_back,
});
