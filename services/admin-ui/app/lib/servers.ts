import { request } from "./api";

export interface ServerAsset {
  id: string; name: string; hostname: string; ssh_port: number; ssh_username: string | null;
  description: string; tags: string[]; revision: number; created_at: string; updated_at: string;
  access_enabled: boolean; allow_sudo: boolean; ssh_allowed: boolean;
}
export interface ServerInput {
  name: string; hostname: string; description: string; tags: string[]; ssh_allowed: boolean; revision?: number;
}
export const getServers = (signal?: AbortSignal) =>
  request<{ servers: ServerAsset[] }>(`/api/servers`, { signal });
export const saveServer = (value: ServerInput, id?: string) =>
  request<ServerAsset>(`/api/servers${id ? `/${id}` : ""}`, {
    method: id ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value),
  });
export const deleteServer = (id: string, revision: number) =>
  request<{ id: string }>(`/api/servers/${encodeURIComponent(id)}?revision=${revision}&confirmed=true`, { method: "DELETE" });
