import { afterEach, expect, test, vi } from "vitest";
import { getReviews, reviewReason, startReview } from "~/features/sessions/reviews";

afterEach(() => vi.unstubAllGlobals());

test("reviews are started and listed on the run key", async () => {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push(`${init?.method ?? "GET"} ${url}`);
      return Response.json({ reviews: [] });
    }),
  );
  await startReview("cc:s 1:2");
  await getReviews("cc:s 1:2");
  expect(calls).toEqual(["POST /api/agent-runs/cc%3As%201%3A2/reviews", "GET /api/agent-runs/cc%3As%201%3A2/reviews"]);
});

test("reviewReason: only a saved agent's finished chat or event run", () => {
  const session = { purpose: "chat", agent_id: "ops", status: "waiting" } as const;
  expect(reviewReason(session)).toBeNull();
  expect(reviewReason({ ...session, purpose: "event", status: "failed" })).toBeNull();
  expect(reviewReason({ ...session, status: "closed" })).toBeNull();
  for (const status of ["starting", "working", "retrying"] as const)
    expect(reviewReason({ ...session, status })).toBe("Wait for the agent to finish before reviewing it.");
  for (const other of [{ agent_id: null }, { purpose: "resource" }, { purpose: "review" }, { purpose: "documents" }])
    expect(reviewReason({ ...session, ...other } as never)).toBe(
      "Reviews are for a saved agent's chats and event runs.",
    );
  expect(reviewReason(null)).toBe("Reviews are for a saved agent's chats and event runs.");
});
