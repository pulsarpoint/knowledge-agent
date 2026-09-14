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
export interface RagFolder {
  id: string; path: string; scope: string; description: string; present: boolean;
  indexed_files: number; files: number; indexed_at: string | null;
}
export interface RagFile { path: string; folder: string; size: number; supported: boolean; indexed_hash: string | null; indexed_at: string | null }
export interface RagTree { revision: number; source_root: string; source_location: RagSourceLocation; folders: RagFolder[]; files: RagFile[]; pending_deletions?: string[] }
export interface RagDocument { path: string; content: string; hash: string | null }
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
