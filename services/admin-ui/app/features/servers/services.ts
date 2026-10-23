import { request } from "~/lib/api";
import type { ServiceType, ServiceRole } from "./serviceClassification";
import type { ServerAsset } from "./api";
import type { Status } from "./inventory";

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
export interface ServiceDependency {
  service_id: string;
  name: string;
  description: string;
}
/** The service's own fields, as created and updated through the inventory API. */
export interface ServiceInput {
  service_type: ServiceType;
  service_role: ServiceRole;
  name: string;
  description: string;
  owner: string;
  /** Where an external service is reached; optional. */
  address?: string | null;
}
/** A deployment as the services list shows it: with its environment's name and its server. */
export interface ServiceDeployment {
  id: string;
  server_id: string;
  environment_id: string;
  environment: string;
  runtime: string;
  runtime_name: string;
  port: number | null;
  log_location: string;
  deployed_version: string;
  status: Status;
  /** Its environment's status, and its server's membership's of that environment. */
  environment_status: Status;
  membership_status: Status;
  server: ServerAsset;
}
export interface ServiceAsset {
  service_type: ServiceType;
  service_role: ServiceRole;
  id: string;
  project_id: string;
  name: string;
  description: string;
  owner: string;
  address: string | null;
  status: Status;
  revision: number;
  created_at: string;
  updated_at: string;
  repositories: ServiceRepository[];
  deployments: ServiceDeployment[];
  /** depends_on relations from and to this service. */
  dependencies: ServiceDependency[];
  dependents: ServiceDependency[];
  /** The environments of its deployments. */
  environments: { id: string; name: string }[];
}
export const getServices = (signal?: AbortSignal) => request<{ services: ServiceAsset[] }>(`/api/services`, { signal });
/** One service, as the services list shows it (its agent context carries the same record). */
export const getService = (id: string, signal?: AbortSignal) =>
  request<{ service: ServiceAsset }>(`/api/services/${encodeURIComponent(id)}/context`, { signal }).then(
    (value) => value.service,
  );
export const deleteService = (id: string, revision: number) =>
  request<{ id: string }>(`/api/services/${encodeURIComponent(id)}?revision=${revision}`, {
    method: "DELETE",
  });
