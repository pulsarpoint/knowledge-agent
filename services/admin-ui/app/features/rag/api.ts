import { request } from "~/lib/api";

export interface RagSettings {
  max_file_bytes: number;
}
export interface RagDataRoot {
  container_root: string | null;
  host_root: string | null;
  jira_folder: string | null;
}
export interface RagConfig {
  revision: number;
  settings: RagSettings;
  embedding_model_id: string | null;
  data_root: RagDataRoot;
}
export interface RagJob {
  id: string;
  status: string;
  input: { force?: boolean; auto?: boolean };
  result: Record<string, number>;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  collection: string;
  collection_id: string | null;
}
export interface RagHit {
  id: string;
  text: string;
  position: number;
  path: string;
  collection: string;
  score: number;
  citation: string;
  content_hash: string;
  indexed_at: string;
  job_id: string;
}
export interface RagResults {
  collections: string[];
  fallback: string[];
  results: RagHit[];
}
export function ragRequest<T>(resource: string, method = "GET", body?: unknown, signal?: AbortSignal) {
  return request<T>(`/api/rag/${resource}`, {
    method,
    signal,
    ...(body === undefined ? {} : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  });
}

export type SearchMode = "full_text" | "embedding" | "hybrid";
export type CollectionState = "folder_missing" | "syncing" | "failed" | "needs_refresh" | "up_to_date";
export interface SkippedCounts {
  links: number;
  special: number;
  unreadable: number;
  invalid_names: number;
}
export interface CollectionPath {
  id: string;
  collection_id: string;
  source_path: string;
  created_at: string;
  files: number;
  host_path: string | null;
  container_path: string;
  present: boolean;
}
export interface CollectionSettings {
  description: string;
  search_mode: SearchMode;
  embeddings_enabled: boolean;
  include: string[];
  exclude: string[];
  chunk_size: number;
  chunk_overlap: number;
  auto_sync: boolean;
  poll_interval_minutes: number;
}
export const DEFAULT_SETTINGS: CollectionSettings = {
  description: "",
  search_mode: "full_text",
  embeddings_enabled: false,
  include: ["**/*.md", "**/*.markdown", "**/*.txt"],
  exclude: [],
  chunk_size: 2000,
  chunk_overlap: 200,
  auto_sync: false,
  poll_interval_minutes: 5,
};
export const syncLabel = (c: Pick<CollectionSettings, "auto_sync" | "poll_interval_minutes">) =>
  c.auto_sync ? `Auto · every ${c.poll_interval_minutes} min` : "Manual";
export type LogStatus =
  "indexed" | "skipped_binary" | "skipped_too_large" | "skipped_unreadable" | "failed" | "removed";
export type LogFilter = "indexed" | "skipped" | "failed" | "removed";
export interface LogCounts {
  indexed: number;
  skipped: number;
  failed: number;
}
export interface LogFile {
  path: string;
  status: LogStatus;
  size: number | null;
  mtime: number | null;
  error: string | null;
  indexed_at: string | null;
  last_seen_at: string;
  removed_at: string | null;
  chunks: number;
}
export const LOG_STATUS_LABEL: Record<LogStatus, string> = {
  indexed: "Indexed",
  skipped_binary: "Skipped · binary",
  skipped_too_large: "Skipped · too large",
  skipped_unreadable: "Skipped · unreadable",
  failed: "Failed",
  removed: "Removed",
};
export interface CollectionSummary extends CollectionSettings {
  id: string;
  name: string;
  revision: number;
  synced_revision: number | null;
  last_synced_at: string | null;
  last_polled_at: string | null;
  paths: CollectionPath[];
  folder_present: boolean;
  missing_paths: string[];
  indexed_files: number;
  chunks: number;
  embedded_chunks: number;
  log_counts: LogCounts;
}
export interface RagCollection extends CollectionSummary {
  /** truncated: the walk stopped at the sync caps; too_large: a sync would fail on a limit (files, entries or 200 MB). */
  folder_changed_at: string | null;
  file_count: number;
  truncated: boolean;
  too_large: boolean;
  skipped: SkippedCounts;
  needs_refresh: boolean;
  status: CollectionState;
  job: { id: string; status: string; error: string | null; created_at: string; finished_at: string | null } | null;
}
export interface FolderEntry {
  name: string;
  path: string;
}
export interface FolderListing {
  path: string | null;
  folders: FolderEntry[];
  truncated: boolean;
}

/** A folder path as RAG expects it: relative to DATA_ROOT, no surrounding whitespace or slashes. */
export const normalizeFolder = (value: string) => value.trim().replace(/^\/+|\/+$/g, "");
const RAG_SEGMENT = "[A-Za-z0-9_-][A-Za-z0-9._-]*";
const RAG_FOLDER = new RegExp(`^${RAG_SEGMENT}(/${RAG_SEGMENT})*$`);
/** RAG's source path rule: names of letters, digits, ".", "_" and "-" separated by "/", none starting with ".". */
export const isRagFolder = (value: string) => value.length <= 500 && RAG_FOLDER.test(value);
/** A collection name from a folder's last segment: lowercase letters, digits and single hyphens, at most 60 characters. */
export const collectionNameFor = (folder: string) =>
  (normalizeFolder(folder).split("/").pop() ?? "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60)
    .replace(/-+$/, "");
export const getCollections = (signal?: AbortSignal) =>
  ragRequest<{ collections: RagCollection[] }>("collections", "GET", undefined, signal);
export const getCollectionNames = (signal?: AbortSignal) =>
  ragRequest<{ collections: CollectionSummary[] }>("collections?status=false", "GET", undefined, signal);
export const getCollection = (id: string, signal?: AbortSignal) =>
  ragRequest<RagCollection>(`collections/${id}`, "GET", undefined, signal);
export const createCollection = (value: CollectionSettings & { name: string }) =>
  ragRequest<CollectionSummary>("collections", "POST", value);
export const saveCollectionSettings = (id: string, value: CollectionSettings & { revision: number }) =>
  ragRequest<RagCollection>(`collections/${id}`, "PUT", value);
export const deleteCollection = (id: string, revision: number) =>
  ragRequest<{ deleted_files: number }>(`collections/${id}?revision=${revision}&confirmed=true`, "DELETE");
export const addFolder = (id: string, sourcePath: string) =>
  ragRequest<CollectionSummary>(`collections/${id}/paths`, "POST", { source_path: sourcePath });
export const removeFolder = (id: string, pathId: string) =>
  ragRequest<CollectionSummary>(`collections/${id}/paths/${pathId}`, "DELETE");
export const getFolders = (path: string | null, signal?: AbortSignal) =>
  ragRequest<FolderListing>(
    path === null ? "folders" : `folders?path=${encodeURIComponent(path)}`,
    "GET",
    undefined,
    signal,
  );
export const syncCollection = (id: string, force = false) =>
  ragRequest<{ id: string }>(`collections/${id}/sync`, "POST", { force });
export const syncStale = () => ragRequest<{ queued: string[] }>("collections/sync-stale", "POST");
export function getFileLog(
  id: string,
  query: { status?: LogFilter; q?: string; limit: number; offset: number },
  signal?: AbortSignal,
) {
  const params = new URLSearchParams();
  if (query.status) params.set("status", query.status);
  if (query.q) params.set("q", query.q);
  params.set("limit", String(query.limit));
  params.set("offset", String(query.offset));
  return ragRequest<{ files: LogFile[]; total: number }>(`collections/${id}/log?${params}`, "GET", undefined, signal);
}
export const getChunks = (id: string, path: string) =>
  ragRequest<{ chunks: { position: number; text: string; embedded: boolean }[] }>(
    `collections/${id}/chunks?path=${encodeURIComponent(path)}`,
  );
