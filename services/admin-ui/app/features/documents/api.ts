import { request } from "~/lib/api";
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
type DocumentIds = ResourceIds<Exclude<DocumentKey, "environments">> & Partial<ResourceIds<"environments">>;
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
  current_version_id: string;
  published_version_id: string | null;
  revision: number;
  version: number;
  content_hash: string;
  created_at: string;
  updated_at: string;
  folder?: string | null;
  draft_count?: number;
}
export interface DocumentVersion {
  id: string;
  version: number;
  title: string;
  description: string;
  purpose: string;
  audience: DocumentInput["audience"];
  tags: string[];
  markdown: string;
  content_hash: string;
  source_snapshot: DocumentResources;
  origin: "manual" | "uploaded" | "generated";
  base_version_id: string | null;
  created_at: string;
}
export interface DocumentDetail extends DocumentSummary {
  current_version: DocumentVersion;
  versions: (Pick<
    DocumentVersion,
    "id" | "version" | "title" | "origin" | "base_version_id" | "content_hash" | "created_at"
  > & { from_draft?: boolean })[];
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
export const getDocumentVersion = (id: string, versionId: string, signal?: AbortSignal) =>
  request<DocumentVersion>(`/api/documentation/documents/${id}/versions/${versionId}`, { signal });
export const saveDocument = (value: DocumentInput, id?: string) =>
  request<{ id: string; revision: number; version_id: string }>(`/api/documentation/documents${id ? `/${id}` : ""}`, {
    method: id ? "PUT" : "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(value),
  });
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
export const SAME_DOCUMENT_NAME =
  "A document with this name already exists in this folder. Confirm to save a new version.";
export const FOLDER_RULE =
  "A documentation folder is a path such as payments or team/payments: each segment uses lowercase letters, digits, '.', '_' and '-' and does not start with '.'; node_modules and a first segment services are not allowed; at most 200 characters.";
// Lowercase-only, unlike RAG folders: on case-insensitive filesystems Payments and payments are one directory.
const FOLDER_SEGMENT = /^[a-z0-9_-][a-z0-9._-]*$/;
/** The server's documentation folder rule: '' when valid, else the rule text. */
export function folderError(folder: string) {
  const parts = folder.split("/");
  return folder.length > 200 ||
    parts.some((part) => !FOLDER_SEGMENT.test(part) || part === "node_modules") ||
    parts[0] === "services"
    ? FOLDER_RULE
    : "";
}
export const documentSlug = (name: string) =>
  name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 120)
    .replace(/-+$/, "");
/** The default documentation folder for a code project: its name as a slug plus -docs ("Payments API" → "payments-api-docs"). */
export function defaultFolder(name: string) {
  const base = name
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, "-")
    .replace(/^[-.]+|-+$/g, "")
    .slice(0, 195)
    .replace(/-+$/, "");
  return base ? `${base}-docs` : "docs";
}

export interface DocumentationFolder {
  folder: string;
  document_count: number;
  updated_at: string;
  last_generated_at: string | null;
  code_projects: string[];
  source_path: string | null;
}
export interface DocumentationFolders {
  generated_folder: string | null;
  folders: DocumentationFolder[];
}
export interface FolderDocument {
  id: string;
  slug: string;
  title: string;
  version: number;
  updated_at: string;
  last_generated_at: string | null;
  code_projects: string[];
}
const folderUrl = (folder: string) =>
  `/api/documentation/folders/${folder.split("/").map(encodeURIComponent).join("/")}`;
export const getDocumentationFolders = (signal?: AbortSignal) =>
  request<DocumentationFolders>("/api/documentation/folders", { signal });
export const getDocumentationFolder = (folder: string, signal?: AbortSignal) =>
  request<{ folder: string; documents: FolderDocument[] }>(folderUrl(folder), { signal });

export const exportDocument = (id: string) =>
  request<{ folder: string }>(`/api/documentation/documents/${id}/export`, { method: "POST" });
