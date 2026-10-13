import { request } from './api';
import type { DocumentInput } from './documents';
import type { SessionPurpose } from './sessions';

export type DraftOrigin = 'manual' | 'agent';
export interface DraftSource {
  kind: 'parent' | 'read'; source_document_id: string | null; source_version_id: string | null;
  source_title: string; source_version: number | null; deleted: boolean;
}
export interface DraftSummary {
  id: string; document_id: string | null; slug: string | null; folder: string | null; title: string; origin: DraftOrigin;
  session_id: string | null; session_purpose?: SessionPurpose | null; session_title: string | null; author: string | null; edited: boolean;
  status: 'open' | 'promoted' | 'discarded'; revision: number; base_version_id: string | null;
  base_version: number | null; live_version: number | null; stale: boolean; created_at: string; updated_at: string;
}
export type DraftContent = Pick<DocumentInput, 'title' | 'description' | 'purpose' | 'audience' | 'tags' | 'markdown'>;
export interface DraftDetail extends DraftSummary, DraftContent {
  resource_ids: Record<string, string[]>; sources: DraftSource[]; live_markdown: string | null; document_title: string | null;
  promoted_version_id: string | null; decided_at: string | null;
}
export type DraftCreate = Partial<DraftContent> & {
  document_id?: string; slug?: string; folder?: string | null;
  resource_ids?: { code_project_ids: string[]; service_ids: string[]; server_ids: string[]; deployment_ids: string[]; environment_ids: string[] };
};
export interface PromoteResult { draft: DraftDetail; document_id: string; version_id: string; revision: number; export_error: string | null }
export interface LinkedDocument { id: string; title: string; slug: string; folder: string | null; version: number }
export interface Connections { depends_on: LinkedDocument[]; used_by: LinkedDocument[]; built_from: DraftSource[] }
/** The error code of a promote refused because the live version moved on; Promote anyway confirms it. */
export const STALE_BASE = 'STALE_BASE';

const json = (method: string, value: unknown) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) });
const base = '/api/documentation';
const draftUrl = (id: string) => `${base}/drafts/${encodeURIComponent(id)}`;
const documentUrl = (id: string) => `${base}/documents/${encodeURIComponent(id)}`;

export const getNewDrafts = (signal?: AbortSignal) => request<{ drafts: DraftSummary[] }>(`${base}/drafts?new=true`, { signal });
export const getDocumentDrafts = (documentId: string, signal?: AbortSignal) =>
  request<{ drafts: DraftSummary[] }>(`${documentUrl(documentId)}/drafts`, { signal });
export const getDraft = (id: string, signal?: AbortSignal) => request<DraftDetail>(draftUrl(id), { signal });
export const createDraft = (value: DraftCreate) => request<DraftDetail>(`${base}/drafts`, json('POST', value));
export const updateDraft = (id: string, revision: number, content: DraftContent) =>
  request<DraftDetail>(draftUrl(id), json('PUT', { revision, ...content }));
export const promoteDraft = (id: string, revision: number, confirmStale: boolean) =>
  request<PromoteResult>(`${draftUrl(id)}/promote`, json('POST', { revision, confirm_stale: confirmStale }));
export const discardDraft = (id: string, revision: number) => request<DraftDetail>(`${draftUrl(id)}/discard`, json('POST', { revision }));
export const getConnections = (documentId: string, signal?: AbortSignal) =>
  request<Connections>(`${documentUrl(documentId)}/connections`, { signal });
export const addLink = (documentId: string, toId: string) =>
  request<Connections>(`${documentUrl(documentId)}/links`, json('POST', { to_document_id: toId }));
export const removeLink = (documentId: string, toId: string) =>
  request<Connections>(`${documentUrl(documentId)}/links/${encodeURIComponent(toId)}`, { method: 'DELETE' });
/** Where a draft opens: its document's Drafts tab, or the page of a new document's draft. */
export const draftPath = (draft: { id: string; document_id: string | null }) => draft.document_id
  ? `/documents/${draft.document_id}?tab=drafts&draft=${draft.id}` : `/documents/drafts/${draft.id}`;
/** What the document page says after a draft was promoted, with the export error when writing the file failed. */
export const promotedNotice = (result: PromoteResult) =>
  `Draft promoted to version ${result.revision}.${result.export_error ? ` ${result.export_error}` : ''}`;
