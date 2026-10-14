import { request } from '~/lib/api';

/** What Apply would replace on the original (section labels) and the fingerprint the dialog sends back. */
export interface ApplyPreview { original: { id: string; name: string; revision: number }; fingerprint: string; changes: string[] }
const path = (id: string) => `/api/agents/${encodeURIComponent(id)}`;
const post = (value?: unknown) => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, ...(value === undefined ? {} : { body: JSON.stringify(value) }) });
export const cloneAgent = (id: string) => request<{ id: string; name: string }>(path(id) + '/clone', post());
export const getApplyPreview = (id: string) => request<ApplyPreview>(path(id) + '/apply');
export const applyToOriginal = (id: string, preview: ApplyPreview) =>
  request<{ id: string; revision: number }>(path(id) + '/apply', post({ revision: preview.original.revision, fingerprint: preview.fingerprint }));
