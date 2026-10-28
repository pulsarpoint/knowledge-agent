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
  /** It revises a live document (false: a brand-new draft, "Not published"). */
  published?: boolean;
  /** The live document's title (null for a brand-new draft). */
  document_title?: string | null;
  /** The draft's own tags. */
  tags: string[];
  created_at: string;
  updated_at: string;
}
export type DraftContent = Pick<DocumentInput, "title" | "description" | "purpose" | "audience" | "tags" | "markdown">;
export interface DraftDetail extends DraftSummary, DraftContent {
  resource_ids: Record<string, string[]>;
  sources: DraftSource[];
  live_markdown: string | null;
}
export type DraftCreate = Partial<DraftContent> & {
  document_id?: string;
  /** A draft of a document: the version the page loaded (default the live one). */
  base_version?: number;
  slug?: string;
  resource_ids?: ResourceIds<DocumentKey>;
};
export interface PromoteResult {
  document_id: string;
  version: number;
  revision: number;
  /** The document's other drafts the promote deleted. */
  deleted_drafts: number;
  /** The promoted draft (added here; the server's answer does not repeat it). */
  draft_id: string;
}
/** GET /drafts filters: only brand-new documents' drafts, the drafts linked to a resource, the drafts with a tag. */
export interface DraftFilter {
  new?: boolean;
  tag?: string;
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
  version: number;
}
export interface Connections {
  depends_on: LinkedDocument[];
  used_by: LinkedDocument[];
  built_from: DraftSource[];
}
/** The error code of a promote refused because the live version moved on; Publish anyway confirms it. */
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
/** A draft's text at its revision; a brand-new draft may also change its slug until it is published. */
export const updateDraft = (id: string, revision: number, content: DraftContent, slug?: string) =>
  request<DraftDetail>(draftUrl(id), json("PUT", { revision, ...(slug ? { slug } : {}), ...content }));
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
export const promoteWarning = (others: number) => `Publish and delete ${others} other draft(s)?`;
export const getConnections = (documentId: string, signal?: AbortSignal) =>
  request<Connections>(`${documentUrl(documentId)}/connections`, { signal });
export const addLink = (documentId: string, toId: string) =>
  request<Connections>(`${documentUrl(documentId)}/links`, json("POST", { to_document_id: toId }));
export const removeLink = (documentId: string, toId: string) =>
  request<Connections>(`${documentUrl(documentId)}/links/${encodeURIComponent(toId)}`, { method: "DELETE" });
/** Where a draft opens: its document's Drafts sub-page, or a brand-new draft's own page (/documents/{id}, the id the
 * document keeps when it is published). */
export const draftPath = (draft: { id: string; document_id: string | null }) =>
  draft.document_id ? `/documents/${draft.document_id}/drafts?draft=${draft.id}` : `/documents/${draft.id}`;
/** What a page says after Publish made a brand-new draft the live document. */
export const publishedNotice = (result: PromoteResult) => `Published as version ${result.version}.`;
/** What the document page says after a draft of a live document was published (promoted). */
export const promotedNotice = (result: PromoteResult) => `Draft published as version ${result.version}.`;

/** A draft a conversation created or edited (GET /drafts?session_id=): a new document (draft_of null) or a draft of
 * an existing document, and what became of it; title null: a discarded new document (nothing keeps its title). */
export interface SessionDraft {
  id: string;
  title: string | null;
  draft_of: string | null;
  draft_of_title: string | null;
  /** null: unknown (its document was deleted). */
  state: "open" | "published" | "discarded" | "replaced" | null;
  updated_at: string;
}
export const getSessionDrafts = (sessionId: string, signal?: AbortSignal) =>
  request<{ drafts: SessionDraft[] }>(`${base}/drafts?${new URLSearchParams({ session_id: sessionId })}`, { signal });
const STATE_LABELS = { open: "Draft", published: "Published", discarded: "Discarded", replaced: "Replaced" };
export const stateLabel = (state: SessionDraft["state"]) => (state ? STATE_LABELS[state] : "Deleted");
/** How a person mentions a draft in the chat: its title and its full id, which the document tools take. */
export const mentionText = (draft: Pick<SessionDraft, "id" | "title">) =>
  `[Draft "${draft.title ?? "Untitled"}" · ${draft.id}]`;
/** `value` with `text` in place of the selection (none: at the end), set apart by a space from the words beside it;
 * `cursor`: just after the text. */
export function insertMention(value: string, text: string, selection: { start: number; end: number } | null) {
  const start = selection?.start ?? value.length;
  const end = selection?.end ?? value.length;
  const before = value.slice(0, start);
  const after = value.slice(end);
  const lead = before && !/\s$/.test(before) ? " " : "";
  const trail = after && !/^\s/.test(after) ? " " : "";
  return { value: `${before}${lead}${text}${trail}${after}`, cursor: before.length + lead.length + text.length };
}
/** Where a conversation's draft opens: an open draft its draft page, a published one its document (a new document
 * keeps the draft's id); null when it is gone (discarded, replaced or deleted). */
export const sessionDraftPath = (draft: SessionDraft) =>
  draft.state === "open"
    ? `/documents/drafts/${encodeURIComponent(draft.id)}`
    : draft.state === "published"
      ? `/documents/${encodeURIComponent(draft.draft_of ?? draft.id)}`
      : null;
/** Whether the draft's id still names something the agent can use: an open draft, or a published new document. */
export const mentionable = (draft: SessionDraft) =>
  draft.state === "open" || (draft.state === "published" && draft.draft_of === null);
