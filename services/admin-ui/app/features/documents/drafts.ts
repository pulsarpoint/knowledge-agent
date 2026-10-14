import { request } from "~/lib/api";
import type { DocumentInput } from "./api";
import type { DocumentKey, ResourceIds } from "~/lib/resources";
import type { SessionPurpose } from "~/features/sessions/api";

export type DraftOrigin = "manual" | "agent";
export interface DraftSource {
  kind: "parent" | "read";
  source_document_id: string | null;
  source_title: string;
  source_version: number | null;
  deleted: boolean;
}
export interface DraftSummary {
  id: string;
  document_id: string | null;
  slug: string | null;
  folder: string | null;
  title: string;
  origin: DraftOrigin;
  session_id: string | null;
  session_purpose?: SessionPurpose | null;
  session_title: string | null;
  author: string | null;
  edited: boolean;
  revision: number;
  base_version: number | null;
  live_version: number | null;
  stale: boolean;
  created_at: string;
  updated_at: string;
}
export type DraftContent = Pick<DocumentInput, "title" | "description" | "purpose" | "audience" | "tags" | "markdown">;
export interface DraftDetail extends DraftSummary, DraftContent {
  resource_ids: Record<string, string[]>;
  sources: DraftSource[];
  live_markdown: string | null;
  document_title: string | null;
}
export type DraftCreate = Partial<DraftContent> & {
  document_id?: string;
  slug?: string;
  folder?: string | null;
  resource_ids?: ResourceIds<DocumentKey>;
};
export interface PromoteResult {
  document_id: string;
  version: number;
  revision: number;
  /** The document's other drafts the promote deleted. */
  deleted_drafts: number;
  export_error: string | null;
  /** The promoted draft (added here; the server's answer does not repeat it). */
  draft_id: string;
}
/** GET /drafts filters: only brand-new documents' drafts, or the drafts linked to a resource. */
export interface DraftFilter {
  new?: boolean;
  environment_id?: string;
  service_id?: string;
  server_id?: string;
  code_project_id?: string;
  deployment_id?: string;
}
export interface LinkedDocument {
  id: string;
  title: string;
  slug: string;
  folder: string | null;
  version: number;
}
export interface Connections {
  depends_on: LinkedDocument[];
  used_by: LinkedDocument[];
  built_from: DraftSource[];
}
/** The error code of a promote refused because the live version moved on; Promote anyway confirms it. */
export const STALE_BASE = "STALE_BASE";

const json = (method: string, value: unknown) => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(value),
});
const base = "/api/documentation";
const draftUrl = (id: string) => `${base}/drafts/${encodeURIComponent(id)}`;
const documentUrl = (id: string) => `${base}/documents/${encodeURIComponent(id)}`;

export function getDrafts(filter: DraftFilter, signal?: AbortSignal) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filter)) if (value) params.set(key, String(value));
  return request<{ drafts: DraftSummary[] }>(`${base}/drafts${params.size ? `?${params}` : ""}`, { signal });
}
export const getNewDrafts = (signal?: AbortSignal) => getDrafts({ new: true }, signal);
export const getDocumentDrafts = (documentId: string, signal?: AbortSignal) =>
  request<{ drafts: DraftSummary[] }>(`${documentUrl(documentId)}/drafts`, { signal });
export const getDraft = (id: string, signal?: AbortSignal) => request<DraftDetail>(draftUrl(id), { signal });
export const createDraft = (value: DraftCreate) => request<DraftDetail>(`${base}/drafts`, json("POST", value));
export const updateDraft = (id: string, revision: number, content: DraftContent) =>
  request<DraftDetail>(draftUrl(id), json("PUT", { revision, ...content }));
export const promoteDraft = (id: string, revision: number, confirmStale: boolean) =>
  request<Omit<PromoteResult, "draft_id">>(
    `${draftUrl(id)}/promote`,
    json("POST", { revision, confirm_stale: confirmStale }),
  ).then((result): PromoteResult => ({ ...result, draft_id: id }));
export const discardDraft = (id: string, revision: number) =>
  request<Record<string, never>>(`${draftUrl(id)}/discard`, json("POST", { revision }));
/** How many other drafts promoting this draft deletes: the other drafts of its document (a new document has none). */
export async function otherDrafts(draft: { id: string; document_id: string | null }) {
  if (!draft.document_id) return 0;
  const { drafts } = await getDocumentDrafts(draft.document_id);
  return drafts.filter((item) => item.id !== draft.id).length;
}
/** The question before a promote that deletes other drafts. */
export const promoteWarning = (others: number) => `Promote and delete ${others} other draft(s)?`;
export const getConnections = (documentId: string, signal?: AbortSignal) =>
  request<Connections>(`${documentUrl(documentId)}/connections`, { signal });
export const addLink = (documentId: string, toId: string) =>
  request<Connections>(`${documentUrl(documentId)}/links`, json("POST", { to_document_id: toId }));
export const removeLink = (documentId: string, toId: string) =>
  request<Connections>(`${documentUrl(documentId)}/links/${encodeURIComponent(toId)}`, { method: "DELETE" });
/** Where a draft opens: its document's Drafts tab, or the page of a new document's draft. */
export const draftPath = (draft: { id: string; document_id: string | null }) =>
  draft.document_id ? `/documents/${draft.document_id}?tab=drafts&draft=${draft.id}` : `/documents/drafts/${draft.id}`;
/** What the document page says after a draft was promoted, with the export error when writing the file failed. */
export const promotedNotice = (result: PromoteResult) =>
  `Draft promoted to version ${result.version}.${result.export_error ? ` ${result.export_error}` : ""}`;
