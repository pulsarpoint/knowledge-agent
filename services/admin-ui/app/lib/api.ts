import type { ServiceType, ServiceRole } from "./serviceClassification";

export interface AdminSession { is_admin: boolean }
export class ApiError extends Error {
  status: number;
  code?: string;
  constructor(message: string, status: number, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export async function request<T>(url: string, init?: RequestInit, responseType: "json" | "text" = "json"): Promise<T> {
  const response = init ? await fetch(url, init) : await fetch(url);
  if (!response.ok) {
    let message = `request failed (${response.status})`;
    let code: string | undefined;
    try {
      const body = await response.json();
      if (body?.error?.message) message = body.error.message;
      if (typeof body?.error?.code === "string") code = body.error.code;
    } catch { /* non-JSON error body — keep the status message */ }
    throw new ApiError(message, response.status, code);
  }
  if (response.status === 204) return undefined as T;
  if (responseType === "text") return response.text() as Promise<T>;
  return response.json() as Promise<T>;
}

export const getAdminSession = () => request<AdminSession>("/api/session");

export interface Repository {
  id: string; name: string; remote_url: string | null; requested_ref: string | null;
  has_token: boolean; current_commit: string | null; checkout_path: string | null;
  synced_at: string | null; job_id: string | null; queued_at: string | null;
  sync_status: "queued" | "running" | "waiting_input" | "succeeded" | "failed" | "cancelled" | null;
  sync_error: string | null;
}
export interface RepositoryInput {
  remote_url: string; name: string; requested_ref: string | null; token?: string | null;
}
export const getRepositories = (signal?: AbortSignal) =>
  request<{ repositories: Repository[] }>(`/api/repositories`, { signal });
export const saveRepository = (value: RepositoryInput, id?: string) =>
  request<{ id: string }>(`/api/repositories${id ? `/${id}` : ""}`, {
    method: id ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value),
  });
export const pullRepository = (id: string) =>
  request<{ job_id: string }>(`/api/repositories/${id}/pull`, { method: "POST" });

export interface ServerAsset {
  id: string; name: string; hostname: string; ssh_port: number; ssh_username: string | null;
  description: string; tags: string[]; revision: number; created_at: string; updated_at: string;
  access_enabled: boolean; allow_sudo: boolean; ssh_allowed: boolean;
}
export interface ServerInput {
  name: string; hostname: string; description: string; tags: string[]; ssh_allowed: boolean; revision?: number;
}
export interface ServiceRepository {
  id: string; name: string; remote_url: string | null; requested_ref: string | null;
  current_commit: string | null; checkout_path: string | null; synced_at: string | null; archived: boolean;
}
export interface DeploymentInput {
  id?: string; server_id: string; environment: string; runtime: string; runtime_name: string;
  environment_id?: string | null;
  port: number | null; log_location: string; deployed_version: string;
}
export interface ServiceDependencyInput {
  service_id: string; description: string;
}
export interface ServiceInput {
  service_type: ServiceType;
  service_role: ServiceRole;
  name: string; description: string; owner: string; operations_notes: string;
  repository_ids: string[]; deployments: DeploymentInput[]; revision?: number;
  environment_ids?: string[];
  dependencies: ServiceDependencyInput[]; dependents: ServiceDependencyInput[];
}
export interface ServiceAsset {
  service_type: ServiceType;
  service_role: ServiceRole;
  id: string; project_id: string; name: string; description: string; owner: string;
  operations_notes: string; revision: number; created_at: string; updated_at: string;
  repositories: ServiceRepository[]; deployments: (DeploymentInput & { id: string; server: ServerAsset })[];
  dependencies: (ServiceDependencyInput & { name: string })[];
  dependents: (ServiceDependencyInput & { name: string })[];
  environments: { id: string; name: string }[];
}
export const getServices = (signal?: AbortSignal) =>
  request<{ services: ServiceAsset[] }>(`/api/services`, { signal });
export const getServers = (signal?: AbortSignal) =>
  request<{ servers: ServerAsset[] }>(`/api/servers`, { signal });
export const saveServer = (value: ServerInput, id?: string) =>
  request<ServerAsset>(`/api/servers${id ? `/${id}` : ""}`, {
    method: id ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value),
  });
export const deleteServer = (id: string, revision: number) =>
  request<{ id: string }>(`/api/servers/${encodeURIComponent(id)}?revision=${revision}&confirmed=true`, { method: "DELETE" });
export const saveService = (value: ServiceInput, id?: string) =>
  request<{ id: string; revision: number }>(`/api/services${id ? `/${id}` : ""}`, {
    method: id ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value),
  });
export const deleteService = (id: string, revision: number) =>
  request<{ id: string }>(`/api/services/${encodeURIComponent(id)}?revision=${revision}&confirmed=true`, { method: "DELETE" });

export interface EnvironmentInput {
  name: string; description: string; service_ids: string[]; server_ids: string[]; revision?: number;
}
export interface EnvironmentAsset {
  id: string; project_id: string; name: string; description: string; revision: number;
  created_at: string; updated_at: string;
  services: { id: string; name: string }[];
  servers: { id: string; name: string; hostname: string }[];
}
export const getEnvironments = (signal?: AbortSignal) =>
  request<{ environments: EnvironmentAsset[] }>("/api/environments", { signal });
export const saveEnvironment = (value: EnvironmentInput, id?: string) =>
  request<EnvironmentAsset>(`/api/environments${id ? `/${encodeURIComponent(id)}` : ""}`, {
    method: id ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value),
  });
export const deleteEnvironment = (id: string, revision: number) =>
  request<{ id: string }>(`/api/environments/${encodeURIComponent(id)}?revision=${revision}&confirmed=true`, { method: "DELETE" });

export interface JiraConnection {
  id: string; base_url: string; username: string; has_token: boolean; revision: number; jql: string;
  last_tested_at: string | null; last_test_error: string | null; last_successful_sync_at: string | null;
  last_fetch_limit: number | null; last_fetch_total: number | null;
}
export interface JiraSettingsInput {
  base_url: string; username: string; token?: string | null; revision?: number;
}
export interface JiraTicket {
  id: string; issue_key: string; jira_project_key: string; issue_type: string | null; labels: string[]; summary: string; status_name: string | null;
  priority: string | null; assignee_name: string | null; source_updated_at: string;
}
export interface JiraTickets {
  connection: JiraConnection | null; tickets: JiraTicket[];
  export_folder: string | null;
}
export const getJiraSettings = (signal?: AbortSignal) =>
  request<{ connection: JiraConnection | null }>(`/api/jira/settings`, { signal });
export const saveJiraSettings = (value: JiraSettingsInput) =>
  request<{ connection: JiraConnection }>(`/api/jira/settings`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value),
  });
export const testJiraConnection = () =>
  request<{ ok: boolean; user: { username: string; display_name: string }; tested_at: string }>(`/api/jira/test`, { method: "POST" });
export const getJiraTickets = (signal?: AbortSignal) =>
  request<JiraTickets>(`/api/jira/tickets`, { signal });
export const fetchJiraTickets = (limit: number, jql: string) =>
  request<JiraTickets>(`/api/jira/tickets/fetch`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ limit, jql }),
  });

export interface JiraExportResult {
  issue_key: string; path: string; status: "created" | "updated" | "unchanged"; hash: string; comments: number;
}
export const exportJiraTicket = (ticketId: string, connectionRevision: number, signal?: AbortSignal) =>
  request<JiraExportResult>(`/api/jira/tickets/${encodeURIComponent(ticketId)}/export`, {
    method: "POST", signal, headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ connection_revision: connectionRevision }),
  });

