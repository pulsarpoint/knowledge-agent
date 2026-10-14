import { request } from "~/lib/api";
import type { ServiceType, ServiceRole } from "./serviceClassification";
import type { ServerAsset } from "./api";

export interface ServiceRepository {
  id: string;
  name: string;
  remote_url: string | null;
  requested_ref: string | null;
  current_commit: string | null;
  checkout_path: string | null;
  synced_at: string | null;
  archived: boolean;
}
export interface DeploymentInput {
  id?: string;
  server_id: string;
  environment: string;
  runtime: string;
  runtime_name: string;
  environment_id?: string | null;
  port: number | null;
  log_location: string;
  deployed_version: string;
}
export interface ServiceDependencyInput {
  service_id: string;
  description: string;
}
export interface ServiceInput {
  service_type: ServiceType;
  service_role: ServiceRole;
  name: string;
  description: string;
  owner: string;
  operations_notes: string;
  repository_ids: string[];
  deployments: DeploymentInput[];
  revision?: number;
  environment_ids?: string[];
  dependencies: ServiceDependencyInput[];
  dependents: ServiceDependencyInput[];
}
export interface ServiceAsset {
  service_type: ServiceType;
  service_role: ServiceRole;
  id: string;
  project_id: string;
  name: string;
  description: string;
  owner: string;
  operations_notes: string;
  revision: number;
  created_at: string;
  updated_at: string;
  repositories: ServiceRepository[];
  deployments: (DeploymentInput & { id: string; server: ServerAsset })[];
  dependencies: (ServiceDependencyInput & { name: string })[];
  dependents: (ServiceDependencyInput & { name: string })[];
  environments: { id: string; name: string }[];
}
export const getServices = (signal?: AbortSignal) => request<{ services: ServiceAsset[] }>(`/api/services`, { signal });
export const saveService = (value: ServiceInput, id?: string) =>
  request<{ id: string; revision: number }>(`/api/services${id ? `/${id}` : ""}`, {
    method: id ? "PUT" : "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(value),
  });
export const deleteService = (id: string, revision: number) =>
  request<{ id: string }>(`/api/services/${encodeURIComponent(id)}?revision=${revision}&confirmed=true`, {
    method: "DELETE",
  });
