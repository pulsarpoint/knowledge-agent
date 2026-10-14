import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import routes from "~/routes";
import DiscoveryRunRoute from "~/routes/discoveryRun";
import { runPagePath } from "~/features/servers/discovery";
import SessionView from "../SessionView";

vi.mock("../Markdown", () => ({ default: ({ content }: { content: string }) => <div>{content}</div> }));

class Stream extends EventTarget { close = vi.fn(); constructor(public url: string) { super(); } }
beforeEach(() => vi.stubGlobal("EventSource", Stream));
afterEach(() => vi.unstubAllGlobals());

test("a discovery run link is a child of the server page, and its route renders nothing of its own", () => {
  const server = routes.find(item => item.path === "servers/:serverId")!;
  expect(server.file).toBe("./routes/server.tsx");
  expect(server.children?.map(item => [item.path, item.file])).toEqual([["discovery/:runId", "./routes/discoveryRun.tsx"]]);
  expect(routes.some(item => item.path === "servers/:serverId/discovery/:runId")).toBe(false);
  expect(DiscoveryRunRoute()).toBeNull();
  expect(runPagePath("s 1", "r1")).toBe("/servers/s%201/discovery/r1");
});

test("a discovery run's session page says it started from the server, linking to the server page on that run", async () => {
  const detail = { id: "r1", title: "Discovery · web-1", purpose: "discovery", status: "waiting", status_message: "", turns: 1, input_tokens: 0,
    output_tokens: 0, cached_tokens: 0, model_calls: 0, reasoning_tokens: 0, draft: null, draft_turn: null, document_id: null, document_draft: null,
    worker_active: false, queued: 0, document_name: null, folder: null, agent_id: null, agent_name: "Ad-hoc agent", model_id: "m1", model_name: "GPT",
    document_title: null, target_document_id: null, server_id: "s1", run_resources: null, prompt_snapshot: { system_prompt: "" },
    context_snapshot: { folders: [], code_project_ids: [], instructions: "" }, tool_counts: [], inputs: [],
    created_at: "2026-10-11T10:00:00Z", last_activity_at: "2026-10-11T10:00:00Z", closed_at: null };
  vi.stubGlobal("fetch", vi.fn(async (url: string) => url.includes("/events/history")
    ? Response.json({ events: [], has_more: false }) : Response.json(detail)));
  render(<MemoryRouter><SessionView sessionId="r1" /></MemoryRouter>);
  expect(await screen.findByRole("link", { name: "the server" })).toHaveAttribute("href", "/servers/s1/discovery/r1");
});
