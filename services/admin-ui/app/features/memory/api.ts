import { request } from "~/lib/api";

export interface MemoryEntry {
  id: string;
  name: string;
  metadata: Record<string, string>;
  content: string;
  revision: number;
  created_at: string;
  updated_at: string;
  created_by_agent: string | null;
  created_by_agent_name: string | null;
  updated_by_agent: string | null;
  updated_by_agent_name: string | null;
  updated_by_actor: string | null;
  updated_by_email: string | null;
  embedding_profile: string | null;
  score?: number;
}
export interface MemoryVersion {
  revision: number;
  name: string;
  metadata: Record<string, string>;
  content: string;
  by_agent_name: string | null;
  by_email: string | null;
  changed_at: string;
}
export interface MemoryFilters {
  query: string;
  metadata: Record<string, string>;
  agent: string;
}
export interface MemoryDraft {
  name: string;
  metadata: Record<string, string>;
  content: string;
}
export interface MemoryList {
  memories: MemoryEntry[];
  next_offset: number | null;
  total: number;
  not_embedded: number;
  embedding_model: boolean;
  current_profile: string | null;
}

/** The API's message for a stale revision; the only conflict that Load current resolves. */
export const STALE_MESSAGE = "This entry changed since you opened it.";
/** Embedded means embedded with the current model's profile; a vector of another profile counts as not embedded. */
export const isEmbedded = (entry: Pick<MemoryEntry, "embedding_profile">, currentProfile: string | null) =>
  currentProfile !== null && entry.embedding_profile === currentProfile;

export const NO_MEMORY_FILTERS: MemoryFilters = { query: "", metadata: {}, agent: "" };
const json = (body: unknown, method = "POST"): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export function savedBy(entry: Pick<MemoryEntry, "updated_by_agent_name" | "updated_by_email">): string {
  return entry.updated_by_agent_name ?? entry.updated_by_email ?? "—";
}

/** The API's page size: a request for it leaves `limit` out. */
export const MEMORY_PAGE = 20;

export function getMemories(filters: MemoryFilters, offset = 0, signal?: AbortSignal, limit = MEMORY_PAGE) {
  const query = new URLSearchParams();
  if (filters.query.trim()) query.set("query", filters.query.trim());
  if (Object.keys(filters.metadata).length) query.set("metadata", JSON.stringify(filters.metadata));
  if (filters.agent) query.set("agent", filters.agent);
  if (offset > 0) query.set("offset", String(offset));
  if (limit !== MEMORY_PAGE) query.set("limit", String(limit));
  const suffix = query.toString();
  return request<MemoryList>(`/api/memories${suffix ? `?${suffix}` : ""}`, { signal });
}
export const getMemoryAgents = (signal?: AbortSignal) =>
  request<{ agents: { id: string; name: string }[] }>("/api/memories/agents", { signal });
/** The Memory page with this entry open. */
export const memoryHref = (id: string) => `/memory?entry=${encodeURIComponent(id)}`;
export const getMemory = (id: string, signal?: AbortSignal) =>
  request<{ memory: MemoryEntry; versions: MemoryVersion[]; current_profile: string | null }>(`/api/memories/${id}`, {
    signal,
  });
export const createMemory = (draft: MemoryDraft) => request<{ memory: MemoryEntry }>("/api/memories", json(draft));
export const saveMemory = (id: string, draft: MemoryDraft, revision: number) =>
  request<{ memory: MemoryEntry }>(`/api/memories/${id}`, json({ ...draft, revision }, "PUT"));
export const restoreMemory = (id: string, revision: number, currentRevision: number) =>
  request<{ memory: MemoryEntry }>(
    `/api/memories/${id}/restore`,
    json({ revision, current_revision: currentRevision }),
  );
export const deleteMemory = (id: string, revision: number) =>
  request<void>(`/api/memories/${id}?revision=${revision}`, { method: "DELETE" });
export const reembedMemories = () =>
  request<{ embedded: number; failed: number }>("/api/memories/re-embed", { method: "POST" });
export const getMemorySettings = (signal?: AbortSignal) =>
  request<{ embedding_model_id: string | null }>("/api/memory/settings", { signal });
export const saveMemorySettings = (embeddingModelId: string | null) =>
  request<{ embedding_model_id: string | null }>(
    "/api/memory/settings",
    json({ embedding_model_id: embeddingModelId }, "PUT"),
  );
