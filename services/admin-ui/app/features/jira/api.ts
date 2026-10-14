import { request } from "~/lib/api";

export interface JiraConnection {
  id: string;
  base_url: string;
  username: string;
  has_token: boolean;
  revision: number;
  jql: string;
  last_tested_at: string | null;
  last_test_error: string | null;
  last_successful_sync_at: string | null;
  last_fetch_limit: number | null;
  last_fetch_total: number | null;
}
export interface JiraSettingsInput {
  base_url: string;
  username: string;
  token?: string | null;
  revision?: number;
}
export interface JiraTicket {
  id: string;
  issue_key: string;
  jira_project_key: string;
  issue_type: string | null;
  labels: string[];
  summary: string;
  status_name: string | null;
  priority: string | null;
  assignee_name: string | null;
  source_updated_at: string;
}
export interface JiraTickets {
  connection: JiraConnection | null;
  tickets: JiraTicket[];
  export_folder: string | null;
}
export const getJiraSettings = (signal?: AbortSignal) =>
  request<{ connection: JiraConnection | null }>(`/api/jira/settings`, { signal });
export const saveJiraSettings = (value: JiraSettingsInput) =>
  request<{ connection: JiraConnection }>(`/api/jira/settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(value),
  });
export const testJiraConnection = () =>
  request<{ ok: boolean; user: { username: string; display_name: string }; tested_at: string }>(`/api/jira/test`, {
    method: "POST",
  });
export const getJiraTickets = (signal?: AbortSignal) => request<JiraTickets>(`/api/jira/tickets`, { signal });
export const fetchJiraTickets = (limit: number, jql: string) =>
  request<JiraTickets>(`/api/jira/tickets/fetch`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ limit, jql }),
  });

export interface JiraExportResult {
  issue_key: string;
  path: string;
  status: "created" | "updated" | "unchanged";
  hash: string;
  comments: number;
}
export const exportJiraTicket = (ticketId: string, connectionRevision: number, signal?: AbortSignal) =>
  request<JiraExportResult>(`/api/jira/tickets/${encodeURIComponent(ticketId)}/export`, {
    method: "POST",
    signal,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ connection_revision: connectionRevision }),
  });
