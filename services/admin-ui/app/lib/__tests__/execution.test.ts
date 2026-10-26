import { afterEach, expect, test, vi } from "vitest";
import { deleteAgent, describeHistory, getAgentHistory } from "~/features/agents/api";
afterEach(() => vi.unstubAllGlobals());

test("agent deletion sends revision and explicit confirmation and accepts 204", async () => {
  const fetchMock = vi.fn(async () => new Response(null, { status: 204 }));
  vi.stubGlobal("fetch", fetchMock);
  await expect(deleteAgent("custom", 3)).resolves.toBeUndefined();
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/agents/custom",
    expect.objectContaining({ method: "DELETE", body: JSON.stringify({ revision: 3, confirmed: true }) }),
  );
});

test("an agent is deleted with its history only when asked, and its history reads as a short list", async () => {
  const fetchMock = vi.fn(async (url: string) =>
    url.endsWith("/history")
      ? Response.json({ conversations: 2, documentation_sessions: 1, run_records: 1 })
      : new Response(null, { status: 204 }),
  );
  vi.stubGlobal("fetch", fetchMock);
  await deleteAgent("custom", 3, true);
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/agents/custom",
    expect.objectContaining({
      method: "DELETE",
      body: JSON.stringify({ revision: 3, confirmed: true, with_history: true }),
    }),
  );
  const history = await getAgentHistory("custom");
  expect(fetchMock.mock.lastCall?.[0]).toBe("/api/agents/custom/history");
  expect(describeHistory(history)).toBe("2 conversations, 1 documentation session, 1 run record");
  expect(describeHistory({ conversations: 0, documentation_sessions: 0, run_records: 0 })).toBe("");
  expect(describeHistory({ conversations: 1, documentation_sessions: 0, run_records: 0 })).toBe("1 conversation");
});
