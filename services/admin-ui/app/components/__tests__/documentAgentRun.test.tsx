import { afterEach, expect, test, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, MemoryRouter, RouterProvider } from "react-router";
import DocumentAgentRunView, { savedIn } from "../DocumentAgentRunView";
import DocumentAgentRuns from "../DocumentAgentRuns";
import { DraftAuthor } from "../DraftView";
import type { Turn } from "~/lib/turns";

const live = vi.hoisted(() => ({ purpose: "revision" }));
vi.mock("~/lib/useSessionEvents", () => ({
  useSessionEvents: () => ({ session: { id: "r1", title: "Add a section on log rotation.", status: "waiting", purpose: live.purpose,
    agent_name: "Ad-hoc agent", model_name: "GPT", turns: 2, target_document_id: "doc-1", config: { model: { name: "GPT" } },
    document_draft: { id: "d1", document_id: "doc-1", status: "open" } },
    setSession: vi.fn(), error: "", setError: vi.fn(), events: [], older: "start", loadOlder: vi.fn(), toolCounts: undefined, reconnecting: false }),
  useSessionNotice: () => ({ notice: null, clear: vi.fn(), stopping: vi.fn(), sent: vi.fn() }),
}));
const turn = (n: number, work: Turn["work"]): Turn => ({ turn: n, firstId: n, input: null, work, toolCalls: work.length, answer: "Done.",
  grounding: null, status: "completed", error: null, usage: { input: 0, output: 0, cached: 0, reasoning: 0, total: 0, calls: 0 }, subagents: [] });
const saved = turn(1, [{ id: 1, kind: "tool", text: "", name: "save_doc", preview: "Saved draft (3 lines)." }]);
const chat = turn(2, [{ id: 2, kind: "tool", text: "", name: "get_doc", preview: "Title: Runbook" }]);
vi.mock("../RunWorkspace", () => ({ default: ({ heading, headerActions, sidebar, answerActions }: Record<string, any>) =>
  <div>{heading}{headerActions}{sidebar}<ol aria-label="Turns">{[saved, chat].map(t => <li key={t.turn}>{answerActions(t)}</li>)}</ol></div> }));

function api(routes: Record<string, () => [number, unknown]>) {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${url}`;
    if (!(key in routes)) throw new Error(`unexpected ${key}`);
    const [status, value] = routes[key]();
    return new Response(JSON.stringify(value), { status });
  }));
}
afterEach(() => { vi.unstubAllGlobals(); live.purpose = "revision"; });
const at = "2026-10-12T10:00:00Z";

test("the run page links the document, shows the draft and notes a turn without a save", async () => {
  const user = userEvent.setup();
  api({ "GET /api/documentation/documents/doc-1": () => [200, { id: "doc-1", title: "Payments runbook" }],
    "GET /api/documentation/drafts/d1": () => [200, { id: "d1", document_id: "doc-1", base_version: 2, stale: true, status: "open" }] });
  render(<MemoryRouter><DocumentAgentRunView documentId="doc-1" runId="r1" /></MemoryRouter>);
  expect(await screen.findByRole("link", { name: "Payments runbook" })).toHaveAttribute("href", "/documents/doc-1");
  const panel = screen.getByRole("region", { name: "Draft panel" });
  expect(await within(panel).findByText(/based on version 2/)).toBeInTheDocument();
  expect(within(panel).getByText("stale")).toBeInTheDocument();
  expect(within(panel).getByRole("link", { name: "Open draft" })).toHaveAttribute("href", "/documents/doc-1?tab=drafts&draft=d1");
  const turns = within(screen.getByRole("list", { name: "Turns" })).getAllByRole("listitem");
  expect(within(turns[0]).queryByText("No draft saved in this turn")).toBeNull();
  expect(within(turns[1]).getByText("No draft saved in this turn")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Show config" }));
  expect((await screen.findByLabelText("Run configuration")).textContent).toContain('"name": "GPT"');
});

test("a session that is not an Ask agent run redirects to the Generate documents page", async () => {
  live.purpose = "documents";
  // React Router's Node Request cannot consume jsdom's AbortSignal (same workaround as ragJobs.test.tsx).
  const NativeRequest = globalThis.Request;
  vi.stubGlobal("Request", class extends NativeRequest { constructor(input: RequestInfo | URL, init?: RequestInit) { super(input, { ...init, signal: undefined }); } });
  api({ "GET /api/documentation/documents/doc-1": () => [200, { id: "doc-1", title: "Payments runbook" }] });
  const router = createMemoryRouter([{ path: "/documents/:documentId/agent/:runId", element: <DocumentAgentRunView documentId="doc-1" runId="r1" /> },
    { path: "/documentation/generations/:id", element: <p>Generation</p> }], { initialEntries: ["/documents/doc-1/agent/r1"] });
  render(<RouterProvider router={router} />);
  expect(await screen.findByText("Generation")).toBeInTheDocument();
});

test("savedIn counts only a successful save_doc or edit_doc", () => {
  expect(savedIn(saved)).toBe(true);
  expect(savedIn(chat)).toBe(false);
  expect(savedIn(turn(3, [{ id: 3, kind: "tool", text: "", name: "edit_doc", preview: "This text is not in the document.", isError: true }]))).toBe(false);
});

test("the Agent runs tab lists the runs with their drafts", async () => {
  api({ "GET /api/agent-sessions?document_id=doc-1": () => [200, { sessions: [
    { id: "r2", title: "Remove the old backup steps.", status: "waiting", model_name: "GPT", started_by: "a@b.c", created_at: at, last_activity_at: at, draft_id: "d1" },
    { id: "r1", title: "Add a section on log rotation.", status: "closed", model_name: "Claude", started_by: null, created_at: at, last_activity_at: at, draft_id: null }] }] });
  render(<MemoryRouter><DocumentAgentRuns documentId="doc-1" /></MemoryRouter>);
  const first = await screen.findByRole("link", { name: "Remove the old backup steps." });
  expect(first).toHaveAttribute("href", "/documents/doc-1/agent/r2");
  expect(screen.getByText(/Waiting for you · GPT · a@b.c/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Open draft" })).toHaveAttribute("href", "/documents/doc-1?tab=drafts&draft=d1");
  expect(screen.getAllByRole("link", { name: "Open draft" })).toHaveLength(1);
});

test("an empty Agent runs tab says how to start one", async () => {
  api({ "GET /api/agent-sessions?document_id=doc-1": () => [200, { sessions: [] }] });
  render(<MemoryRouter><DocumentAgentRuns documentId="doc-1" /></MemoryRouter>);
  expect(await screen.findByText("No agent runs yet. Ask agent starts one.")).toBeInTheDocument();
});

test("a draft written by an Ask agent run links to its run page", () => {
  const base = { origin: "agent" as const, session_id: "s1", session_title: "Revise", author: null, document_id: "doc-1" };
  const { rerender } = render(<MemoryRouter><DraftAuthor draft={{ ...base, session_purpose: "revision" }} /></MemoryRouter>);
  expect(screen.getByRole("link", { name: "Agent run" })).toHaveAttribute("href", "/documents/doc-1/agent/s1");
  rerender(<MemoryRouter><DraftAuthor draft={{ ...base, session_purpose: "documents" }} /></MemoryRouter>);
  expect(screen.getByRole("link", { name: "Agent run" })).toHaveAttribute("href", "/documentation/generations/s1");
});
