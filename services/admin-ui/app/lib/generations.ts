import { request } from './api';
import type { SessionDetail } from './sessions';

const json = (method: string, value: unknown) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) });

export const DEFAULT_SYSTEM_PROMPT = 'You write technical documentation for software systems. Read the code, documents and servers you were given before you write. Cite evidence as file:line, document path or command. Say what you could not verify.\n\nWhen the document covers several parts (services, code projects, servers), plan the sections first. For each part, ask an expert when its description fits; otherwise start a subagent with run_subagent. Give each a complete task: the section\'s purpose, what to cover, and only that part\'s resources. Write small single-part documents yourself. Combine the answers into one consistent document; check that they agree and resolve or mark conflicts.';
/** What a generation documents. */
export interface Selection { code_project_ids: string[]; collection_ids: string[]; environment_ids: string[]; service_ids: string[]; server_ids: string[] }
/** Docs → Generate documents → New: an ad-hoc native agent with this model, system prompt and context; the prompt is its first message. */
export interface SessionInput extends Selection {
  model_id: string; system_prompt: string; context: string; prompt: string; prompt_id: string | null; document_name: string; folder: string;
}

const sessionsUrl = '/api/generations/sessions';
export const createSession = (value: SessionInput) => request<SessionDetail>(sessionsUrl, json('POST', value));
export const getGenerationContext = (selection: Selection, signal?: AbortSignal) =>
  request<{ text: string }>('/api/generations/context', { ...json('POST', selection), signal });
