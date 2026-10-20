import { request } from "~/lib/api";

/** A proposed new shared memory entry. */
export interface MemoryNote {
  name: string;
  content: string;
  metadata: Record<string, string>;
}
/** A proposed note with the memory entry that already has its name (if any). */
export interface ProposedNote extends MemoryNote {
  exists: boolean;
  memory_id: string | null;
  current_content: string | null;
}
export interface ReviewSource {
  suggestion: string;
  changes: { instructions: string | null };
  memory_notes: ProposedNote[];
  revision: number;
  fingerprint: string;
  instructions: string;
}
export interface ApplyResult {
  agent_id: string;
  saved: { id: string; name: string }[];
  refused: { name: string; message: string }[];
}
const path = (agentId: string, reviewId: string) =>
  `/api/agents/${encodeURIComponent(agentId)}/reviews/${encodeURIComponent(reviewId)}`;
export const getReviewSource = (agentId: string, reviewId: string) => request<ReviewSource>(path(agentId, reviewId));
/** Apply the instructions (null: keep them) and save the notes as new memory entries. */
export const applyReview = (
  agentId: string,
  reviewId: string,
  source: ReviewSource,
  target: "agent" | "clone",
  instructions: string | null,
  memoryNotes: MemoryNote[],
) =>
  request<ApplyResult>(path(agentId, reviewId) + "/apply", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      target,
      revision: source.revision,
      fingerprint: source.fingerprint,
      instructions,
      memory_notes: memoryNotes,
    }),
  });
