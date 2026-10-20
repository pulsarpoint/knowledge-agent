import { request } from "~/lib/api";

export type NotificationStatus = "succeeded" | "failed";
export interface NotificationRow {
  id: number;
  created_at: string;
  kind: string;
  status: NotificationStatus;
  title: string;
  preview: string;
  event_id: number | null;
  session_id: string | null;
  agent_id: string | null;
  agent_name: string | null;
  jira_status?: "pending" | "posted" | "failed" | null;
  jira_issue_key?: string | null;
  /** Giving the ticket back after the comment: skipped when someone else took the ticket meanwhile. */
  give_back_status?: "pending" | "done" | "failed" | "skipped" | null;
}
export interface NotificationDetail extends NotificationRow {
  body: string;
  handler_id: string | null;
  jira_comment_id?: string | null;
  jira_attempts?: number;
  jira_error?: string | null;
  jira_base_url?: string | null;
  give_back_name?: string | null;
  give_back_error?: string | null;
  event: { id: number; type: string; subject: string } | null;
  task: { id: string; attempts: number; created_at: string; finished_at: string | null } | null;
}
export interface NotificationFilters {
  status: string;
  q: string;
}

export const NO_NOTIFICATION_FILTERS: NotificationFilters = { status: "", q: "" };
export const NOTIFICATION_STATUS_LABELS: Record<NotificationStatus, string> = {
  succeeded: "Succeeded",
  failed: "Failed",
};

export function getNotifications(filters: NotificationFilters, offset = 0, signal?: AbortSignal) {
  const query = new URLSearchParams();
  if (filters.status) query.set("status", filters.status);
  if (filters.q.trim()) query.set("q", filters.q.trim());
  if (offset > 0) query.set("offset", String(offset));
  const suffix = query.toString();
  return request<{ notifications: NotificationRow[]; next_offset: number | null }>(
    `/api/notifications${suffix ? `?${suffix}` : ""}`,
    { signal },
  );
}
export const getNotification = (id: number, signal?: AbortSignal) =>
  request<{ notification: NotificationDetail }>(`/api/notifications/${id}`, { signal });
