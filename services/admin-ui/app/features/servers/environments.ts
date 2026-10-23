import { request } from "~/lib/api";
import type { Status } from "./inventory";

export interface EnvironmentInput {
  name: string;
  description: string;
  revision?: number;
}
/** An environment as saved; EnvironmentAsset adds its services (those deployed in it) and its member servers. */
export interface Environment {
  id: string;
  project_id: string;
  name: string;
  description: string;
  status: Status;
  revision: number;
  created_at: string;
  updated_at: string;
}
/** An environment with the services deployed in it and its member servers (with or without deployments). */
export interface EnvironmentAsset {
  id: string;
  project_id: string;
  name: string;
  description: string;
  status: Status;
  revision: number;
  created_at: string;
  updated_at: string;
  services: { id: string; name: string; status: Status }[];
  servers: { id: string; name: string; hostname: string; status: Status }[];
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

/** A deployment in the environment model: on its server, with its service's name. */
export interface ModelDeployment {
  id: string;
  service_id: string;
  service_name: string;
  runtime: string;
  runtime_name: string;
  port: number | null;
  version: string;
  log_location: string;
  status: Status;
  revision: number;
}
/** A member server and its deployments in the environment (none for an empty member). */
export interface ModelServer {
  id: string;
  name: string;
  hostname: string;
  description: string;
  tags: string[];
  status: Status;
  revision: number;
  /** Whether its membership of the environment is confirmed or a draft. */
  membership_status: Status;
  deployments: ModelDeployment[];
}
export interface ModelService {
  id: string;
  name: string;
  service_type: string;
  service_role: string;
  description: string;
  owner: string;
  status: Status;
  revision: number;
}
export interface ModelConnection {
  id: string;
  from_deployment_id: string;
  to_deployment_id: string | null;
  to_host: string | null;
  to_service_id: string | null;
  to_service_name: string | null;
  port: number | null;
  protocol: string;
  description: string;
  status: Status;
  revision: number;
  /** The end in another environment, when there is one. */
  outside?: { deployment_id: string; environment: string; server: string; service: string };
}
export interface ModelRelation {
  id: string;
  kind: "depends_on" | "part_of" | "built_from";
  from_service_id: string;
  from_service: string | null;
  to_service_id: string | null;
  to_service: string | null;
  to_code_project_id: string | null;
  to_code_project: string | null;
  description: string;
  status: Status;
  revision: number;
}
/** One environment as GET /environments/{id}/model reads it (admin-api inventory/model.py). */
export interface EnvironmentModel {
  environment: { id: string; name: string; description: string; status: Status; revision: number };
  servers: ModelServer[];
  services: ModelService[];
  connections: ModelConnection[];
  relations: ModelRelation[];
  external: { host: string; ports: number[] }[];
  external_services: {
    id: string;
    name: string;
    address: string | null;
    description: string;
    status: Status;
    revision: number;
    ports: number[];
  }[];
  /** How many of its entries are drafts (a server's membership counts on its own). */
  drafts: number;
}
const membership = (environmentId: string, serverId: string) =>
  `/api/environments/${encodeURIComponent(environmentId)}/servers/${encodeURIComponent(serverId)}`;
export const getEnvironmentModel = (id: string, signal?: AbortSignal) =>
  request<EnvironmentModel>(`/api/environments/${encodeURIComponent(id)}/model`, { signal });
/** Puts the server in the environment (also when it is there already). */
export const addServerToEnvironment = (environmentId: string, serverId: string) =>
  request<void>(membership(environmentId, serverId), { method: "PUT" });
/** Takes the server out of the environment; refused while it has deployments there. */
export const removeServerFromEnvironment = (environmentId: string, serverId: string) =>
  request<void>(membership(environmentId, serverId), { method: "DELETE" });
