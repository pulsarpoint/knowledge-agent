import { request } from "~/lib/api";
import { type ServerAsset } from "./api";
import type { Status } from "./inventory";

export interface ServerDeployment {
  id: string;
  service_id: string;
  service_name: string;
  environment: string;
  runtime: string;
  runtime_name: string;
  port: number | null;
  deployed_version: string;
  status: Status;
  revision: number;
}
export interface ServerPageData {
  server: ServerAsset;
  environments: { id: string; name: string }[];
  deployments: ServerDeployment[];
  ssh_ready: boolean;
  discovery: { ready: boolean; reason: string | null };
}

const server = (id: string) => `/api/servers/${encodeURIComponent(id)}`;
export const getServerPage = (id: string, signal?: AbortSignal) => request<ServerPageData>(server(id), { signal });
