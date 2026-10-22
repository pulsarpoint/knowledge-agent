import { request } from "~/lib/api";

export interface EnvironmentInput {
  name: string;
  description: string;
  revision?: number;
}
/** An environment as saved; its services and servers are those deployed in it (EnvironmentAsset). */
export interface Environment {
  id: string;
  project_id: string;
  name: string;
  description: string;
  revision: number;
  created_at: string;
  updated_at: string;
}
/** An environment with the services and servers deployed in it. */
export interface EnvironmentAsset {
  id: string;
  project_id: string;
  name: string;
  description: string;
  revision: number;
  created_at: string;
  updated_at: string;
  services: { id: string; name: string }[];
  servers: { id: string; name: string; hostname: string }[];
}
export const getEnvironments = (signal?: AbortSignal) =>
  request<{ environments: EnvironmentAsset[] }>("/api/environments", { signal });
export const getEnvironment = (id: string, signal?: AbortSignal) =>
  request<EnvironmentAsset>(`/api/environments/${encodeURIComponent(id)}`, { signal });
export const saveEnvironment = (value: EnvironmentInput, id?: string) =>
  request<Environment>(`/api/environments${id ? `/${encodeURIComponent(id)}` : ""}`, {
    method: id ? "PUT" : "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(value),
  });
export const deleteEnvironment = (id: string, revision: number) =>
  request<{ id: string }>(`/api/environments/${encodeURIComponent(id)}?revision=${revision}`, {
    method: "DELETE",
  });
