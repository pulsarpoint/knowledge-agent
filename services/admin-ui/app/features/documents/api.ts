import { ApiError, request } from "~/lib/api";
import type { DocumentKey, ResourceIds } from "~/lib/resources";

export interface DocumentResource {
  id: string;
  name: string;
  hostname?: string;
  service_id?: string;
  server_id?: string;
  environment?: string;
  git_commit?: string;
}
export type DocumentResources = Record<Exclude<DocumentKey, "environments">, DocumentResource[]> & {
  environments?: DocumentResource[];
};
/** A document's linked resources: every kind but environments is always sent (environments came later). */
export type DocumentIds = ResourceIds<Exclude<DocumentKey, "environments">> & Partial<ResourceIds<"environments">>;
export interface DocumentInput extends DocumentIds {
  slug: string;
  title: string;
  description: string;
  purpose: string;
  audience: "people" | "agents" | "both";
  tags: string[];
  markdown: string;
  revision?: number;
  source_commits?: Record<string, string>;
}
export interface DocumentSummary extends Omit<DocumentInput, "markdown" | "revision"> {
  id: string;
  revision: number;
  version: number;
  content_hash: string;
  created_at: string;
  updated_at: string;
  draft_count?: number;
}
export type DocumentOrigin = "manual" | "uploaded" | "generated";
/** One version's content, by number: the live row (`live`) or an archived one. */
export interface DocumentVersion {
  version: number;
  title: string;
  description: string;
  purpose: string;
  audience: DocumentInput["audience"];
  tags: string[];
  markdown: string;
  content_hash: string;
  source_snapshot: DocumentResources;
  origin: DocumentOrigin;
  from_draft: boolean;
  session_id: string | null;
  created_by: string | null;
  created_at: string;
  live: boolean;
}
export type ResourceType = "environment" | "service" | "server" | "code_project" | "deployment";
/** A link between the document and a resource: built for it (made with the document, never removed) or linked later. */
export interface ResourceLink {
  type: ResourceType;
  id: string;
  name: string | null;
  connection: "built_for" | "linked";
  added_how: "person" | "agent_run" | "discovery" | "migrated";
  added_by: string | null;
  session_id: string | null;
  created_at: string;
}
export interface ResourceLinkChange extends ResourceLink {
  removed_at: string | null;
  removed_by: string | null;
}
/** A draft of the document as the document page lists it (newest first). */
export interface DocumentDraftRow {
  id: string;
  title: string;
  origin: "manual" | "agent";
  session_id: string | null;
  author: string | null;
  edited: boolean;
  base_version: number | null;
  stale: boolean;
  created_at: string;
  updated_at: string;
}
/** GET /documents/{id}: a live document (is_draft false, published true) or a brand-new draft (is_draft true,
 * published false: no version, snapshot, versions or drafts until it is published). */
export interface DocumentDetail extends Omit<DocumentSummary, "version" | "content_hash"> {
  version?: number;
  content_hash?: string;
  markdown: string;
  source_snapshot?: DocumentResources;
  origin: DocumentOrigin | "agent";
  from_draft?: boolean;
  is_draft: boolean;
  published: boolean;
  session_id?: string | null;
  versions: Pick<DocumentVersion, "version" | "title" | "origin" | "created_at" | "from_draft" | "live">[];
  drafts: DocumentDraftRow[];
  resource_links: ResourceLink[];
}
export const TAG_PATTERN = /^[a-z0-9][a-z0-9-]{0,39}$/;
export const MAX_TAGS = 10;
export interface DocumentationPromptInput {
  name: string;
  description: string;
  tags: string[];
  enabled: boolean;
  content: string;
  revision?: number;
}
export interface DocumentationPrompt extends Omit<DocumentationPromptInput, "content" | "revision"> {
  id: string;
  revision: number;
  active_version_id: string | null;
  active_version: number | null;
  version_count: number;
  updated_at: string;
}
export interface DocumentationPromptVersion {
  id: string;
  version: number;
  content: string;
  activated_at: string | null;
  created_at: string;
}
export interface DocumentationPromptDetail extends DocumentationPrompt {
  versions: DocumentationPromptVersion[];
}
export const getDocumentResources = (signal?: AbortSignal) =>
  request<DocumentResources>("/api/documentation/resources", { signal });
export const getDocuments = (params: URLSearchParams, signal?: AbortSignal) =>
  request<{ documents: DocumentSummary[]; total: number }>(`/api/documentation/documents?${params}`, { signal });
export const getDocument = (id: string, signal?: AbortSignal) =>
  request<DocumentDetail>(`/api/documentation/documents/${id}`, { signal });
export const getDocumentVersion = (id: string, version: number, signal?: AbortSignal) =>
  request<DocumentVersion>(`/api/documentation/documents/${id}/versions/${version}`, { signal });
export const getResourceHistory = (id: string, signal?: AbortSignal) =>
  request<{ history: ResourceLinkChange[] }>(`/api/documentation/documents/${id}/resource-history`, { signal });
/** The message of a save refused because it unticked a built-for link. */
export const isBuiltForRefusal = (e: unknown) =>
  e instanceof ApiError && e.status === 409 && e.message.startsWith("This document was built for");
/** Links of a live document or a brand-new draft (they never go through drafts), at the revision the page holds. The
 * answer's revision is the page's next one: the links route bumps it. */
export const saveDocumentLinks = (id: string, revision: number, ids: DocumentIds) =>
  request<{ id: string; revision: number; resource_links: ResourceLink[] }>(
    `/api/documentation/documents/${encodeURIComponent(id)}/links`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ revision, ...ids }),
    },
  );
/** The kinds of resource an inventory document can be built for. */
export const INVENTORY_TYPES = ["server", "service", "environment"] as const;
export type InventoryType = (typeof INVENTORY_TYPES)[number];
/** The kinds of resource a new document can be built for (a resource page's New document): an inventory kind or a
 * code project. */
export const BUILT_FOR_TYPES = [...INVENTORY_TYPES, "code_project"] as const;
export type BuiltForType = (typeof BUILT_FOR_TYPES)[number];
/** The resource a New document page is for, from its query (?resource=<type>&resource_id=<id>), or null. */
export function newDocumentResource(params: URLSearchParams): { type: BuiltForType; id: string } | null {
  const type = params.get("resource") as BuiltForType | null;
  const id = params.get("resource_id");
  return type && BUILT_FOR_TYPES.includes(type) && id ? { type, id } : null;
}
/** The New document page's query for a resource ("" for none): both sub-pages carry it. */
export const newDocumentSearch = (resource: { type: string; id: string } | null | undefined) =>
  resource ? `?${new URLSearchParams({ resource: resource.type, resource_id: resource.id })}` : "";
/** Write with an agent (/documents/new/agent), on a resource when given; `chat`: the conversation it opens. */
export const writeWithAgentPath = (resource?: { type: string; id: string } | null, chat?: string | null) => {
  const search = new URLSearchParams(newDocumentSearch(resource));
  if (chat) search.set("chat", chat);
  return `/documents/new/agent${search.size ? `?${search}` : ""}`;
};
/** The tag of an inventory document: a resource's inventory document is the one tagged so that is built for it. */
export const INVENTORY_TAG = "inventory";
/** The Documents list filtered by one tag (what every tag badge links to). */
export const tagFilterPath = (tag: string) => `/documents?${new URLSearchParams({ tag })}`;
export const deleteDocument = (id: string, revision: number) =>
  request(`/api/documentation/documents/${id}?revision=${revision}&confirmed=true`, { method: "DELETE" });
export const getDocumentationPrompts = (signal?: AbortSignal, tag?: string) =>
  request<{ prompts: DocumentationPrompt[] }>(
    `/api/documentation/prompts${tag ? `?${new URLSearchParams({ tag })}` : ""}`,
    { signal },
  );
export const getPromptTags = (signal?: AbortSignal) =>
  request<{ tags: { tag: string; count: number }[] }>("/api/documentation/prompts/tags", { signal });
export const getDocumentationPrompt = (id: string, signal?: AbortSignal) =>
  request<DocumentationPromptDetail>(`/api/documentation/prompts/${id}`, { signal });
export const saveDocumentationPrompt = (value: DocumentationPromptInput, id?: string) =>
  request<{ id: string; revision: number; version_id: string }>(`/api/documentation/prompts${id ? `/${id}` : ""}`, {
    method: id ? "PUT" : "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(value),
  });
export const activateDocumentationPrompt = (id: string, versionId: string, revision: number) =>
  request(`/api/documentation/prompts/${id}/versions/${versionId}/activate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ revision }),
  });
export const deleteDocumentationPrompt = (id: string, revision: number, versionId?: string) =>
  request(
    `/api/documentation/prompts/${id}${versionId ? `/versions/${versionId}` : ""}?revision=${revision}&confirmed=true`,
    { method: "DELETE" },
  );
export const documentSlug = (name: string) =>
  name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 120)
    .replace(/-+$/, "");
