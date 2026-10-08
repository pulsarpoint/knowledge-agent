import { request } from './api';

export interface InstructionChanges { system_prompt: string | null; instructions: string | null; server_instructions: { server_id: string; instructions: string }[] }
/** A selected SSH server: `inherits` while its instructions are the agent's additional instructions. */
export interface ReviewServer { id: string; name: string; instructions: string; inherits: boolean }
export interface ReviewSource {
  suggestion: string; changes: InstructionChanges; revision: number; fingerprint: string;
  system_prompt: string; instructions: string; servers: ReviewServer[];
}
const path = (agentId: string, reviewId: string) => `/api/agents/${encodeURIComponent(agentId)}/reviews/${encodeURIComponent(reviewId)}`;
export const getReviewSource = (agentId: string, reviewId: string) => request<ReviewSource>(path(agentId, reviewId));
export const applyReview = (agentId: string, reviewId: string, source: ReviewSource, target: 'agent' | 'clone', changes: InstructionChanges) =>
  request<{ agent_id: string }>(path(agentId, reviewId) + '/apply', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ target, revision: source.revision, fingerprint: source.fingerprint, changes }) });
