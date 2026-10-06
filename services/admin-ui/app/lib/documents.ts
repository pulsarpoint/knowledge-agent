import { request } from './api';

export interface DocumentResource { id: string; name: string; hostname?: string; service_id?: string; server_id?: string; environment?: string; git_commit?: string }
export interface DocumentResources { code_projects: DocumentResource[]; services: DocumentResource[]; servers: DocumentResource[]; deployments: DocumentResource[]; environments?: DocumentResource[] }
export interface DocumentInput {
  slug: string; title: string; description: string; purpose: string; audience: 'people' | 'agents' | 'both';
  tags: string[]; markdown: string; code_project_ids: string[]; service_ids: string[]; server_ids: string[]; deployment_ids: string[]; environment_ids?: string[]; revision?: number;
  source_commits?: Record<string, string>;
}
export interface DocumentSummary extends Omit<DocumentInput, 'markdown' | 'revision'> {
  id: string; current_version_id: string; published_version_id: string | null; revision: number;
  version: number; content_hash: string; created_at: string; updated_at: string;
  folder?: string | null;
}
export interface DocumentVersion {
  id: string; version: number; title: string; description: string; purpose: string; audience: DocumentInput['audience'];
  tags: string[]; markdown: string; content_hash: string; source_snapshot: DocumentResources;
  origin: 'manual' | 'uploaded' | 'generated'; base_version_id: string | null; created_at: string;
}
export interface DocumentDetail extends DocumentSummary { workflows?: {id:string;name:string}[]; current_version: DocumentVersion; versions: Pick<DocumentVersion, 'id' | 'version' | 'title' | 'origin' | 'base_version_id' | 'content_hash' | 'created_at'>[] }
export interface DocumentationPromptInput { name: string; description: string; input_kind: 'code' | 'server' | 'service' | 'mixed'; enabled: boolean; content: string; revision?: number }
export interface DocumentationPrompt extends Omit<DocumentationPromptInput, 'content' | 'revision'> {
  id: string; revision: number; active_version_id: string | null; active_version: number | null; version_count: number; updated_at: string;
}
export interface DocumentationPromptVersion { id: string; version: number; content: string; activated_at: string | null; created_at: string }
export interface DocumentationPromptDetail extends DocumentationPrompt { versions: DocumentationPromptVersion[] }
export interface DocumentationPhaseUsage {
  phase: string; step: string; requests: number; reported_calls: number;
  input_tokens: number; output_tokens: number; total_tokens: number;
  input_tokens_details?: { cached_tokens?: number; cache_write_tokens?: number };
  output_tokens_details?: { reasoning_tokens?: number };
}
export interface DocumentationGeneration {
  workflow_id?: string; scope?: string; document_name?: string | null; folder?: string | null;
  job_id: string; mode: string; instructions: string; status: string; error: string | null; created_at: string; document_count: number; progress?: string;
  service_id?: string; code_graph_hashes?: Record<string, string>;
  token_usage?: DocumentationPhaseUsage[];
  prompt_snapshot?: { id: string; version_id: string; version: number; name: string; kind: string; content: string; overridden: boolean }[];
  result?: { output_folders?: string[]; export_error?: string | null; documents?: { id: string; revision: number; version_id: string }[] };
}

export interface ApiAnalysis {
  inventory: null | {
    coverage: { id: string; name: string; route_count: number; limitations: string }[];
    batches: { key: string; project_name: string; source_characters?: number; gaps: string[];
      routes: { id: string; name: string; file_path: string; start_line: number }[] }[] };
  sections: { fingerprint: string; batch_key: string; position: number; title: string; markdown: string; reused_from: string | null }[];
}
export const getApiAnalysis = (jobId: string, signal?: AbortSignal) =>
  request<ApiAnalysis>(`/api/documentation/generations/${jobId}/api-analysis`, { signal });

export const getDocumentResources = (signal?: AbortSignal) => request<DocumentResources>('/api/documentation/resources', { signal });
export const getDocuments = (params: URLSearchParams, signal?: AbortSignal) => request<{ documents: DocumentSummary[]; total: number }>(`/api/documentation/documents?${params}`, { signal });
export const getDocument = (id: string, signal?: AbortSignal) => request<DocumentDetail>(`/api/documentation/documents/${id}`, { signal });
export const getDocumentVersion = (id: string, versionId: string, signal?: AbortSignal) => request<DocumentVersion>(`/api/documentation/documents/${id}/versions/${versionId}`, { signal });
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
export const getDocumentationGenerations = (offset: number, signal?: AbortSignal, filter?: { serviceId?: string; kind?: string; workflowId?: string; scope?: string }) => {
  const params = new URLSearchParams({ offset: String(offset) });
  if (filter?.serviceId) params.set('service_id', filter.serviceId);
  if (filter?.kind) params.set('kind', filter.kind);
  if (filter?.workflowId) params.set('workflow_id', filter.workflowId);
  if (filter?.scope) params.set('scope', filter.scope);
  return request<{ generations: DocumentationGeneration[] }>(`/api/documentation/generations?${params}`, { signal });
};

export interface GenerationInput {
  workflow_id?: string;
  code_graph_hashes?: Record<string, string>;
  source_commits?: Record<string, string>;
  scope: 'service' | 'environment'; model_id: string;
  service_id: string | null; environment_id: string | null; code_project_ids: string[]; server_ids: string[];
  units: { kind: 'overview' | 'api' | 'messaging' | 'code' | 'diagram' | 'deployment' | 'deployment_diagram' | 'architecture' | 'infrastructure'; prompt_id: string; prompt_version_id: string; prompt_override?: string }[];
  instructions: string; update_existing: boolean;
}
export const generateDocumentation = (value: GenerationInput | CodeProjectGenerationInput) => request<{ job_id: string }>('/api/documentation/generations', {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value),
});

/** The server's message when the selected prompt version is no longer active (admin-api PROMPT_CHANGED). */
export const PROMPT_CHANGED = 'The prompt changed. Reload the prompt before generating.';
export const SAME_DOCUMENT_NAME = 'A document with this name already exists in this folder. Confirm to save a new version.';
export const FOLDER_RULE = "A documentation folder is a path such as payments or team/payments: each segment uses lowercase letters, digits, '.', '_' and '-' and does not start with '.'; node_modules and a first segment services are not allowed; at most 200 characters.";
// Lowercase-only, unlike RAG folders: on case-insensitive filesystems Payments and payments are one directory.
const FOLDER_SEGMENT = /^[a-z0-9_-][a-z0-9._-]*$/;
/** The server's documentation folder rule: '' when valid, else the rule text. */
export function folderError(folder: string) {
  const parts = folder.split('/');
  return folder.length > 200 || parts.some(part => !FOLDER_SEGMENT.test(part) || part === 'node_modules') || parts[0] === 'services' ? FOLDER_RULE : '';
}
export const documentSlug = (name: string) => name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 120).replace(/-+$/, '');
/** The default documentation folder for a code project: its name as a slug plus -docs ("Payments API" → "payments-api-docs"). */
export function defaultFolder(name: string) {
  const base = name.toLowerCase().replace(/[^a-z0-9._-]+/g, '-').replace(/^[-.]+|-+$/g, '').slice(0, 195).replace(/-+$/, '');
  return base ? `${base}-docs` : 'docs';
}

export interface CodeProjectGenerationInput {
  scope: 'code_project'; model_id: string; code_project_id: string; prompt_id: string; prompt_version_id: string;
  code_graph_hashes: Record<string, string>; comments: string; document_name: string; folder: string; update_existing: boolean;
}
export interface DocumentationFolder { folder: string; document_count: number; updated_at: string; last_generated_at: string | null; code_projects: string[]; source_path: string | null }
export interface DocumentationFolders { generated_folder: string | null; folders: DocumentationFolder[] }
export interface FolderDocument { id: string; slug: string; title: string; version: number; updated_at: string; last_generated_at: string | null; code_projects: string[] }
const folderUrl = (folder: string) => `/api/documentation/folders/${folder.split('/').map(encodeURIComponent).join('/')}`;
export const getDocumentationFolders = (signal?: AbortSignal) => request<DocumentationFolders>('/api/documentation/folders', { signal });
export const getDocumentationFolder = (folder: string, signal?: AbortSignal) => request<{ folder: string; documents: FolderDocument[] }>(folderUrl(folder), { signal });

export const exportDocument = (id: string) => request<{ folder: string }>(`/api/documentation/documents/${id}/export`, { method: "POST" });
export type GenerationKind = GenerationInput['units'][number]['kind'];
export interface DocumentationPromptSelection { id: string; versionId: string; version: number; activeContent: string; content: string; error?: string }
export interface DocumentationWorkflow {
  id: string; name: string; scope: 'service' | 'environment'; service_id: string | null; environment_id: string | null; created_at: string; updated_at: string;
  drafts: { form?: { value: GenerationInput; kinds?: GenerationKind[]; environmentSelection?: GenerationKind[]; selection?: Partial<Record<GenerationKind, DocumentationPromptSelection>> };
    overview?: { content: string; instructions: string }; api?: { content: string; instructions: string }; messaging?: { content: string; instructions: string } };
  phases: {kind: GenerationKind;job_id:string;status:string;error:string|null;created_at:string}[];
  graph_runs: {job_id:string;repository_id:string;status:string;error:string|null;result?:{source_hash?:string}}[];
}
export const getDocumentationWorkflows = (offset = 0, signal?: AbortSignal) => request<{workflows: DocumentationWorkflow[]}>(`/api/documentation/workflows?offset=${offset}`, {signal});
export const getDocumentationWorkflow = (id: string, signal?: AbortSignal) => request<DocumentationWorkflow>(`/api/documentation/workflows/${id}`, {signal});
export const createDocumentationWorkflow = (value: {scope:'service'|'environment';service_id:string|null;environment_id:string|null;model_id:string}) => request<{id:string}>('/api/documentation/workflows', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});
export const saveDocumentationDraft = (id:string, section:string, content:object, previous:object|null) => request(`/api/documentation/workflows/${id}/drafts/${section}`, {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({content,previous})});
