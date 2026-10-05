import { request } from "./api";

export interface RagSettings {
  chunk_size: number; chunk_overlap: number; max_file_bytes: number; extensions: string[];
  embedding_enabled: boolean;
}
export interface RagSourceLocation {
  host_root: string | null; container_root: string;
}
export interface RagConfig {
  revision: number; settings: RagSettings; source_root: string; source_location: RagSourceLocation; embedding_model_id: string | null;
}
export interface ProcessInput { folder_id: string; recursive: boolean; force: boolean; allow_empty: boolean }
export interface RagJob {
  id: string; path: string; status: string; input: ProcessInput; result: Record<string, number>;
  error: string | null; created_at: string; started_at: string | null; finished_at: string | null;
  folder_deleted_at: string | null;
}
export interface RagHit {
  id: string; path: string; folder: string; text: string; kind: string; score: number;
  citation: string; content_hash: string; indexed_at: string; job_id: string;
}
export interface RagResults { mode: string; folder: string; results: RagHit[]; }
export function ragRequest<T>(resource: string, method = "GET", body?: unknown, signal?: AbortSignal) {
  return request<T>(`/api/rag/${resource}`, {
    method, signal, ...(body === undefined ? {} : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  });
}

export type SearchMode = "full_text" | "embedding" | "hybrid";
export type CollectionState = "folder_missing" | "syncing" | "failed" | "needs_refresh" | "up_to_date";
export interface CollectionInput {
  name: string; description: string; source_path: string; search_mode: SearchMode; embeddings_enabled: boolean;
  include: string[]; exclude: string[]; chunk_size: number; chunk_overlap: number; revision?: number;
}
export interface SkippedCounts { links: number; special: number; unreadable: number; invalid_names: number }
export interface RagCollection extends Required<CollectionInput> {
  id: string; synced_revision: number | null; last_synced_at: string | null; host_path: string | null;
  container_path: string; writable: boolean; folder_present: boolean; folder_changed_at: string | null;
  file_count: number; truncated: boolean; skipped: SkippedCounts;
  indexed_files: number; chunks: number; embedded_chunks: number; needs_refresh: boolean; status: CollectionState;
  job: { id: string; status: string; error: string | null; created_at: string; finished_at: string | null } | null;
}
export interface CollectionFile {
  path: string; size: number | null; mtime: number | null; indexed_at: string | null;
  content_hash: string | null; chunks: number; embedded_chunks: number; stale: boolean;
}
export const DEFAULT_COLLECTION: CollectionInput = {
  name: "", description: "", source_path: "rag-data/", search_mode: "full_text", embeddings_enabled: false,
  include: ["**/*.md", "**/*.markdown", "**/*.txt"], exclude: [], chunk_size: 2000, chunk_overlap: 200,
};
export const getCollections = (signal?: AbortSignal) => ragRequest<{ collections: RagCollection[] }>("collections", "GET", undefined, signal);
export const getCollection = (id: string, signal?: AbortSignal) => ragRequest<RagCollection>(`collections/${id}`, "GET", undefined, signal);
export const saveCollection = (value: CollectionInput, id?: string) => ragRequest<RagCollection | { id: string }>(id ? `collections/${id}` : "collections", id ? "PUT" : "POST", value);
export const deleteCollection = (id: string, revision: number) => ragRequest<{ deleted_files: number }>(`collections/${id}?revision=${revision}&confirmed=true`, "DELETE");
export const syncCollection = (id: string, force = false) => ragRequest<{ id: string }>(`collections/${id}/sync`, "POST", { force });
export const syncStale = () => ragRequest<{ queued: string[] }>("collections/sync-stale", "POST");
export const getCollectionFiles = (id: string, signal?: AbortSignal) => ragRequest<{ files: CollectionFile[]; truncated: boolean; skipped: SkippedCounts }>(`collections/${id}/files`, "GET", undefined, signal);
export const getChunks = (id: string, path: string) => ragRequest<{ chunks: { position: number; text: string; embedded: boolean }[] }>(`collections/${id}/chunks?path=${encodeURIComponent(path)}`);
export const getRagFile = (collection: string, path: string) => ragRequest<{ collection: string; path: string; content: string; hash: string }>(`file?collection=${encodeURIComponent(collection)}&path=${encodeURIComponent(path)}`);
export const putRagFile = (collection: string, path: string, content: string, expected_hash: string | null) => ragRequest<{ path: string; hash: string }>("file", "PUT", { collection, path, content, expected_hash });
