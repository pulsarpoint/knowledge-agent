import { request } from './api';

export interface DocumentResource { id: string; name: string; hostname?: string; service_id?: string; server_id?: string; environment?: string }
export interface DocumentResources { code_projects: DocumentResource[]; services: DocumentResource[]; servers: DocumentResource[]; deployments: DocumentResource[]; environments?: DocumentResource[] }
export interface DocumentInput {
  slug: string; title: string; description: string; purpose: string; audience: 'people' | 'agents' | 'both';
  tags: string[]; markdown: string; code_project_ids: string[]; service_ids: string[]; server_ids: string[]; deployment_ids: string[]; environment_ids?: string[]; revision?: number;
}
export interface DocumentSummary extends Omit<DocumentInput, 'markdown' | 'revision'> {
  id: string; current_version_id: string; published_version_id: string | null; revision: number;
  version: number; content_hash: string; created_at: string; updated_at: string;
}
export interface DocumentVersion {
  id: string; version: number; title: string; description: string; purpose: string; audience: DocumentInput['audience'];
  tags: string[]; markdown: string; content_hash: string; source_snapshot: DocumentResources;
  origin: 'manual' | 'uploaded' | 'generated'; base_version_id: string | null; created_at: string;
}
export interface DocumentDetail extends DocumentSummary { current_version: DocumentVersion; versions: Pick<DocumentVersion, 'id' | 'version' | 'title' | 'origin' | 'base_version_id' | 'content_hash' | 'created_at'>[] }
export interface DocumentationPromptInput { name: string; description: string; input_kind: 'code' | 'server' | 'service' | 'mixed'; enabled: boolean; content: string; revision?: number }
export interface DocumentationPrompt extends Omit<DocumentationPromptInput, 'content' | 'revision'> {
  id: string; revision: number; active_version_id: string | null; active_version: number | null; version_count: number; updated_at: string;
}
export interface DocumentationPromptVersion { id: string; version: number; content: string; activated_at: string | null; created_at: string }
export interface DocumentationPromptDetail extends DocumentationPrompt { versions: DocumentationPromptVersion[] }
export interface DocumentationGeneration { job_id: string; mode: string; instructions: string; status: string; error: string | null; created_at: string; document_count: number; progress?: string; result?: { output_folders?: string[]; export_error?: string | null; documents?: { id: string; revision: number }[] } }

export const getDocumentResources = (signal?: AbortSignal) => request<DocumentResources>('/api/documentation/resources', { signal });
export const getDocuments = (params: URLSearchParams, signal?: AbortSignal) => request<{ documents: DocumentSummary[]; total: number }>(`/api/documentation/documents?${params}`, { signal });
export const getDocument = (id: string, signal?: AbortSignal) => request<DocumentDetail>(`/api/documentation/documents/${id}`, { signal });
export const getDocumentVersion = (id: string, versionId: string) => request<DocumentVersion>(`/api/documentation/documents/${id}/versions/${versionId}`);
export const saveDocument = (value: DocumentInput, id?: string) => request<{ id: string; revision: number; version_id: string }>(`/api/documentation/documents${id ? `/${id}` : ''}`, {
  method: id ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value),
});
export const deleteDocument = (id: string, revision: number) => request(`/api/documentation/documents/${id}?revision=${revision}&confirmed=true`, { method: 'DELETE' });
export const getDocumentationPrompts = (signal?: AbortSignal) => request<{ prompts: DocumentationPrompt[] }>('/api/documentation/prompts', { signal });
export const getDocumentationPrompt = (id: string, signal?: AbortSignal) => request<DocumentationPromptDetail>(`/api/documentation/prompts/${id}`, { signal });
export const saveDocumentationPrompt = (value: DocumentationPromptInput, id?: string) => request<{ id: string; revision: number; version_id: string }>(`/api/documentation/prompts${id ? `/${id}` : ''}`, {
  method: id ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value),
});
export const activateDocumentationPrompt = (id: string, versionId: string, revision: number) => request(`/api/documentation/prompts/${id}/versions/${versionId}/activate`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision }),
});
export const deleteDocumentationPrompt = (id: string, revision: number, versionId?: string) => request(`/api/documentation/prompts/${id}${versionId ? `/versions/${versionId}` : ''}?revision=${revision}&confirmed=true`, { method: 'DELETE' });
export const getDocumentationGenerations = (offset: number, signal?: AbortSignal) => request<{ generations: DocumentationGeneration[] }>(`/api/documentation/generations?offset=${offset}`, { signal });

export interface GenerationInput {
  scope: 'service' | 'environment'; model_id: string;
  service_id: string | null; environment_id: string | null; code_project_ids: string[]; server_ids: string[];
  units: { kind: 'code' | 'diagram' | 'deployment' | 'architecture' | 'infrastructure'; prompt_id: string; prompt_version_id: string; prompt_override?: string }[];
  instructions: string; update_existing: boolean;
}
export const generateDocumentation = (value: GenerationInput) => request<{ job_id: string }>('/api/documentation/generations', {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value),
});

export const exportDocument = (id: string) => request<{ folder: string }>(`/api/documentation/documents/${id}/export`, { method: "POST" });
