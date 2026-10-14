import { request } from "~/lib/api";

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
