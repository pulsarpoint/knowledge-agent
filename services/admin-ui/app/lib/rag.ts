import { request } from "./api";

export interface RagSettings { max_file_bytes: number }
export interface RagArea { area: string; container_root: string; host_root: string | null }
export interface RagConfig { revision: number; settings: RagSettings; embedding_model_id: string | null; areas: RagArea[] }
export interface RagJob {
  id: string; status: string; input: { force?: boolean }; result: Record<string, number>;
  error: string | null; created_at: string; started_at: string | null; finished_at: string | null;
  collection: string; collection_id: string | null;
}
export interface RagHit {
  id: string; text: string; position: number; path: string; collection: string; score: number;
  citation: string; content_hash: string; indexed_at: string; job_id: string;
}
export interface RagResults { collections: string[]; fallback: string[]; results: RagHit[] }
export function ragRequest<T>(resource: string, method = "GET", body?: unknown, signal?: AbortSignal) {
  return request<T>(`/api/rag/${resource}`, {
    method, signal, ...(body === undefined ? {} : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  });
}

export type SearchMode = "full_text" | "embedding" | "hybrid";
export type CollectionState = "folder_missing" | "syncing" | "failed" | "needs_refresh" | "up_to_date";
export interface SkippedCounts { links: number; special: number; unreadable: number; invalid_names: number }
export type OwnerKind = "documents" | "jira" | "code_project";
export interface CollectionPath {
  id: string; collection_id: string; source_path: string; owner_kind: OwnerKind | null; owner_id: string | null;
  owner_key: string | null; created_at: string; files: number; host_path: string | null; container_path: string; present: boolean;
}
export interface CollectionSettings {
  description: string; search_mode: SearchMode; embeddings_enabled: boolean;
  include: string[]; exclude: string[]; chunk_size: number; chunk_overlap: number;
}
export interface CollectionSummary extends CollectionSettings {
  id: string; name: string; revision: number; synced_revision: number | null; last_synced_at: string | null;
  paths: CollectionPath[]; folder_present: boolean; missing_paths: string[];
  indexed_files: number; chunks: number; embedded_chunks: number;
}
export interface RagCollection extends CollectionSummary {
  folder_changed_at: string | null; file_count: number; truncated: boolean; skipped: SkippedCounts;
  needs_refresh: boolean; status: CollectionState;
  job: { id: string; status: string; error: string | null; created_at: string; finished_at: string | null } | null;
}
export interface CollectionFile {
  path: string; source: string | null; size: number | null; mtime: number | null; indexed_at: string | null;
  content_hash: string | null; chunks: number; embedded_chunks: number; stale: boolean;
}
export const OWNER_LABEL: Record<OwnerKind, string> = { documents: "Documents", jira: "Jira", code_project: "Code project" };
export const ownerLabel = (path: CollectionPath) => path.owner_kind ? OWNER_LABEL[path.owner_kind] : "Unowned";
export const getCollections = (signal?: AbortSignal) => ragRequest<{ collections: RagCollection[] }>("collections", "GET", undefined, signal);
export const getCollectionNames = (signal?: AbortSignal) => ragRequest<{ collections: CollectionSummary[] }>("collections?status=false", "GET", undefined, signal);
export const getCollection = (id: string, signal?: AbortSignal) => ragRequest<RagCollection>(`collections/${id}`, "GET", undefined, signal);
export const saveCollectionSettings = (id: string, value: CollectionSettings & { revision: number }) => ragRequest<RagCollection>(`collections/${id}`, "PUT", value);
export const deleteCollection = (id: string, revision: number) => ragRequest<{ deleted_files: number }>(`collections/${id}?revision=${revision}&confirmed=true`, "DELETE");
export const detachPath = (collection: string, sourcePath: string) =>
  ragRequest<{ removed: number }>(`collections/by-name/${encodeURIComponent(collection)}/paths?source_path=${encodeURIComponent(sourcePath)}`, "DELETE");
export const syncCollection = (id: string, force = false) => ragRequest<{ id: string }>(`collections/${id}/sync`, "POST", { force });
export const syncStale = () => ragRequest<{ queued: string[] }>("collections/sync-stale", "POST");
export const getCollectionFiles = (id: string, signal?: AbortSignal) => ragRequest<{ files: CollectionFile[]; truncated: boolean; skipped: SkippedCounts; missing_paths: string[] }>(`collections/${id}/files`, "GET", undefined, signal);
export const getChunks = (id: string, path: string) => ragRequest<{ chunks: { position: number; text: string; embedded: boolean }[] }>(`collections/${id}/chunks?path=${encodeURIComponent(path)}`);
