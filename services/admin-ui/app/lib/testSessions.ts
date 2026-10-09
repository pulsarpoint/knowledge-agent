import { request } from './api';
import type { SessionDetail, SessionStatus } from './generations';
import type { RunResourcesInput } from './profiles';

/** The agent Test tab's sessions (event handlers' sessions are listed too, purpose `event`): created with the first message, then run by the agent session worker like a
 * Generate documents session (messages, Stop, Close and the event stream are the session API in generations.ts). */
export interface TestSessionSummary { id: string; title: string; status: SessionStatus; purpose: 'test' | 'event'; created_at: string; last_activity_at: string; actor_email: string }
/** A tuning suggestion; `response_turn` is the answer the feedback was about (none: the whole session). */
export interface SessionReview { id: string; comment: string; suggestion: string; response_turn?: number | null; created_at: string }

const json = (value: unknown) => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) });
const sessions = (agentId: string) => `/api/agents/${encodeURIComponent(agentId)}/sessions`;
export const createTestSession = (agentId: string, message: string, runResources?: RunResourcesInput) =>
  request<SessionDetail>(sessions(agentId), json({ message, ...(runResources ? { run_resources: runResources } : {}) }));
export const getTestSessions = (agentId: string) => request<{ sessions: TestSessionSummary[] }>(sessions(agentId));
export const reviewTestSession = (agentId: string, sessionId: string, comment: string, turn?: number) =>
  request<SessionReview>(`${sessions(agentId)}/${encodeURIComponent(sessionId)}/review`, json({ comment, ...(turn ? { turn } : {}) }));
