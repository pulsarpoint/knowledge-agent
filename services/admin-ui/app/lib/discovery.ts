import { request } from './api';
import { type ServerAsset } from './servers';

export interface ServerDeployment { id: string; service_id: string; service_name: string; environment: string; runtime: string;
  runtime_name: string; port: number | null; deployed_version: string }
export interface ServerPageData { server: ServerAsset; environments: { id: string; name: string }[]; deployments: ServerDeployment[];
  ssh_ready: boolean; discovery: { ready: boolean; reason: string | null } }
export type ServiceField = 'service_type' | 'service_role' | 'description' | 'owner' | 'operations_notes';
export const SERVICE_FIELD_LABELS: Record<ServiceField, string> = { service_type: 'Type', service_role: 'Role',
  description: 'Description', owner: 'Owner', operations_notes: 'Operations notes' };
export interface ProposedDeployment { environment_id: string | null; environment: string; runtime: string; runtime_name: string;
  port: number | null; log_location: string; deployed_version: string }
export interface ServicePayload extends Record<ServiceField, string> { name: string; deployment: ProposedDeployment;
  depends_on: { id: string; name: string }[]; built_from: { id: string; name: string }[]; matched_revision: number | null }
export interface DocumentPayload { title: string; description: string; markdown: string; about_service: string | null }
interface ProposalBase { id: string; session_id: string; server_id: string; number: number; status: 'pending' | 'accepted' | 'rejected';
  revision: number; result_id: string | null; decided_at: string | null; service_id: string | null; document_id: string | null;
  service_proposal_id: string | null }
export interface ServiceProposal extends ProposalBase { kind: 'service'; payload: ServicePayload;
  current?: Record<ServiceField, string> & { id: string; name: string; revision: number } | null; changes?: ServiceField[];
  current_deployment?: (Omit<ProposedDeployment, 'environment_id'> & { id: string; environment_id: string | null }) | null }
export interface DocumentProposal extends ProposalBase { kind: 'document'; payload: DocumentPayload; service_name?: string;
  service_proposal_status?: 'pending' | 'accepted' | 'rejected'; current?: { id: string; title: string; version: number } | null }
export type Proposal = ServiceProposal | DocumentProposal;

const json = (method: string, body: unknown) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
const server = (id: string) => `/api/servers/${encodeURIComponent(id)}`;
export const runPagePath = (serverId: string, runId: string) => `/servers/${encodeURIComponent(serverId)}/discovery/${encodeURIComponent(runId)}`;
export const getServerPage = (id: string, signal?: AbortSignal) => request<ServerPageData>(server(id), { signal });
export const getProposals = (runId: string, signal?: AbortSignal) =>
  request<{ proposals: Proposal[] }>(`/api/discovery/${encodeURIComponent(runId)}/proposals`, { signal });
export const acceptProposal = (id: string, revision: number, fields?: ServiceField[]) =>
  request<{ proposal: Proposal; result_id: string; export_error: string | null }>(`/api/discovery/proposals/${encodeURIComponent(id)}/accept`,
    json('POST', fields === undefined ? { revision } : { revision, fields }));
export const rejectProposal = (id: string, revision: number) =>
  request<{ proposal: Proposal; rejected_documents: number }>(`/api/discovery/proposals/${encodeURIComponent(id)}/reject`, json('POST', { revision }));
