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
export interface DocumentPayload {
  title: string;
  description: string;
  markdown: string;
  about_service: string | null;
}
interface ProposalBase {
  id: string;
  session_id: string;
  /** The server of the run that proposed it; null for a service's or an environment's run. */
  server_id: string | null;
  number: number;
  status: "pending" | "accepted" | "rejected";
  revision: number;
  result_id: string | null;
  decided_at: string | null;
  service_id: string | null;
  document_id: string | null;
}
export interface DocumentProposal extends ProposalBase {
  kind: "document";
  payload: DocumentPayload;
  service_name?: string;
  current?: { id: string; title: string; version: number } | null;
  /** The draft accepting made: brand-new (document_id null) or of the live document document_id; null once deleted. */
  draft_id?: string | null;
  /** The draft went live (published or promoted). */
  draft_published?: boolean;
}
export type Proposal = DocumentProposal;

const json = (method: string, body: unknown) => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});
const server = (id: string) => `/api/servers/${encodeURIComponent(id)}`;
export const getServerPage = (id: string, signal?: AbortSignal) => request<ServerPageData>(server(id), { signal });
export const acceptProposal = (id: string, revision: number) =>
  request<{ proposal: Proposal; result_id: string }>(
    `/api/discovery/proposals/${encodeURIComponent(id)}/accept`,
    json("POST", { revision }),
  );
export const rejectProposal = (id: string, revision: number) =>
  request<{ proposal: Proposal }>(
    `/api/discovery/proposals/${encodeURIComponent(id)}/reject`,
    json("POST", { revision }),
  );
