import { request } from './api';

export interface CodeProject {
  id: string; name: string; description: string; remote_url: string | null; requested_ref: string | null;
  clone_path: string | null; folder_paths: string[]; current_commit: string | null; checkout_path: string | null;
  has_token: boolean; synced_at: string | null; sync_status: string | null; sync_error: string | null;
  sync_interval_minutes: number | null; next_sync_at: string | null; revision: number;
}
export interface CodeProjectInput {
  name: string; description: string; remote_url: string | null; requested_ref: string | null;
  clone_path: string | null; folder_paths: string[]; sync_interval_minutes: number | null;
  clone_now: boolean; revision?: number; token?: string | null; username?: string;
}
export interface CodeEntry { name: string; path: string; kind: 'folder' | 'file' | 'symlink' | 'other'; size: number }
export interface CodeDirectory {
  root: string; host_path: string | null; path: string; entries: CodeEntry[]; total: number; next_offset: number | null;
}
export const getCodeProjects = (signal?: AbortSignal) => request<{ projects: CodeProject[] }>('/api/code-projects', { signal });
export const saveCodeProject = (value: CodeProjectInput, id?: string) => request<{ id: string }>(
  `/api/code-projects${id ? `/${encodeURIComponent(id)}` : ''}`,
  { method: id ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) });
export const pullCodeProject = (id: string) => request<{ job_id: string }>(`/api/code-projects/${encodeURIComponent(id)}/pull`, { method: 'POST' });
export const getCodeDirectory = (path: string, offset = 0, signal?: AbortSignal) => request<CodeDirectory>(`/api/code/files?path=${encodeURIComponent(path)}&offset=${offset}`, { signal });
export const getCodeFolders = (path: string, offset: number, signal?: AbortSignal) => request<CodeDirectory>(`/api/code/files?path=${encodeURIComponent(path)}&offset=${offset}&folders_only=true`, { signal });
export const getCodeFile = (path: string, signal?: AbortSignal) => request<{ path: string; text: string }>(`/api/code/file?path=${encodeURIComponent(path)}`, { signal });
