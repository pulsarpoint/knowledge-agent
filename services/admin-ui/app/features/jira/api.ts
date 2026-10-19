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

/** A Jira user (by stable key) mapped to the agent that does the work assigned to them. */
export interface JiraUserAgent {
  jira_user_key: string;
  jira_user_name: string;
  agent_id: string;
  agent_name: string;
  enabled: boolean;
  updated_at: string;
}
export interface JiraUser {
  key: string;
  name: string | null;
  display_name: string;
}
const userAgentPath = (key: string) => `/api/jira/user-agents/${encodeURIComponent(key)}`;
export const getJiraUserAgents = (signal?: AbortSignal) =>
  request<{ user_agents: JiraUserAgent[] }>(`/api/jira/user-agents`, { signal });
/** `name` is the display name; empty when only a typed key is known (the server then shows the key). */
export const saveJiraUserAgent = (key: string, agentId: string, name: string) =>
  request<{ user_agents: JiraUserAgent[] }>(userAgentPath(key), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ agent_id: agentId, name }),
  });
export const deleteJiraUserAgent = (key: string) => request<void>(userAgentPath(key), { method: "DELETE" });
export const searchJiraUsers = (query: string, signal?: AbortSignal) =>
  request<{ users: JiraUser[]; warning: string | null }>(`/api/jira/users?query=${encodeURIComponent(query)}`, {
    signal,
  });
