import { request } from './api';

export type StorageArea = 'code' | 'rag' | 'documents' | 'bootstrap';
export interface StorageLocation {
  id: StorageArea; container_path: string | null; host_path: string | null;
  host_variable: string; host_path_relative: boolean; status: 'available' | 'unavailable' | 'unconfigured';
}
export interface StorageEntry {
  name: string; path: string; kind: 'folder' | 'file' | 'symlink' | 'other'; size: number | null; modified_at: string;
}
export interface StorageDirectory { path: string; entries: StorageEntry[]; total: number; next_offset: number | null }
export const getStorage = (signal?: AbortSignal) => request<{ locations: StorageLocation[] }>('/api/storage', { signal });
export const getStorageDirectory = (area: StorageArea, path: string, offset: number, signal?: AbortSignal) =>
  request<StorageDirectory>(`/api/storage/${area}/entries?${new URLSearchParams({ path, offset: String(offset) })}`, { signal });
export const getStorageFile = (area: StorageArea, path: string, signal?: AbortSignal) =>
  request<{ path: string; text: string }>(`/api/storage/${area}/file?${new URLSearchParams({ path })}`, { signal });
