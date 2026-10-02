import { request } from './api';

export interface ReaderCandidate { id: string; name: string; enabled: boolean; violations: string[] }
export interface ReaderSettings { knowledge_agent_id: string | null; revision: number; updated_at: string | null; candidates: ReaderCandidate[] }

export const getReaderSettings = () => request<ReaderSettings>('/api/reader/settings');
export const saveReaderSettings = (knowledge_agent_id: string, revision: number) =>
  request<ReaderSettings>('/api/reader/settings', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ knowledge_agent_id, revision }),
  });
