import { request } from "~/lib/api";
import { isWorking, type RunSummary, type SessionDetail, type SessionSummary } from "./api";
import { runPath } from "./runs";

/** The texts the API refuses a review with when the page already knows (the button is disabled with them). */
export const REVIEW_BUSY = "Wait for the agent to finish before reviewing it.";
export const REVIEW_NOT_SAVED = "Reviews are for a saved agent's chats and event runs.";

/** One earlier review of a session (a review run: purpose review), with the run key it was started on. */
export type ReviewSummary = Pick<
  SessionSummary,
  "id" | "title" | "agent_id" | "agent_name" | "status" | "status_message" | "turns" | "created_at" | "last_activity_at"
> & { closed_at: string | null; review_of: string };

const reviews = (runKey: string) => `/api/agent-runs/${encodeURIComponent(runKey)}/reviews`;
/** Start a review of the run's session: the review session, waiting for the person's feedback as its first message. */
export const startReview = (runKey: string) => request<SessionDetail>(reviews(runKey), { method: "POST" });
/** The reviews of the run's session (of any of its turns), newest first. */
export const getReviews = (runKey: string, signal?: AbortSignal) =>
  request<{ reviews: ReviewSummary[] }>(reviews(runKey), { signal }).then(
    (value) => value.reviews.map((item) => ({ ...item, purpose: "review" })) as unknown as RunSummary[],
  );

/** The session id of a run key `cc:<session>:<turn>` (null: a run outside a session). */
export const sessionOfRunKey = (runKey: string): string | null => /^cc:([^:]+):\d+$/.exec(runKey)?.[1] ?? null;
/** The run key of a session's latest turn (what Improve agent reviews from the chat). */
export const latestRunKey = (session: Pick<SessionSummary, "id" | "turns">) =>
  `cc:${session.id}:${Math.max(session.turns, 1)}`;

/** Why the session cannot be reviewed (null: it can): only a saved agent's chat or event run, once it is not working.
 * An agent without a usable model is refused by the API on start. */
export function reviewReason(session: Pick<SessionSummary, "purpose" | "agent_id" | "status"> | null): string | null {
  if (!session || session.agent_id === null || (session.purpose !== "chat" && session.purpose !== "event"))
    return REVIEW_NOT_SAVED;
  return isWorking(session.status) ? REVIEW_BUSY : null;
}

/** Where a review continues: the reviewed run's record with the review open in its sheet (`?review=<id>`). */
export const continueReviewPath = (reviewId: string, reviewOf: string) =>
  `${runPath(reviewOf)}?${new URLSearchParams({ review: reviewId })}`;
/** A review session's Continue review link (null: not a review). A review never opens in the agent's chat. */
export function reviewLink(session: { id: string; purpose: string; config?: Record<string, unknown> | null }) {
  const reviewOf = session.config?.review_of;
  return session.purpose === "review" && typeof reviewOf === "string" ? continueReviewPath(session.id, reviewOf) : null;
}
