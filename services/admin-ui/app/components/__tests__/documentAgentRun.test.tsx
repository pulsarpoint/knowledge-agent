import { afterEach, expect, test, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { savedIn } from "~/lib/sessions";
import DocumentAgentRuns from "../DocumentAgentRuns";
import { DraftAuthor } from "../DraftView";
import type { Turn } from "~/lib/turns";

const turn = (n: number, work: Turn["work"]): Turn => ({ turn: n, firstId: n, input: null, work, toolCalls: work.length, answer: "Done.",
  grounding: null, status: "completed", error: null, usage: { input: 0, output: 0, cached: 0, reasoning: 0, total: 0, calls: 0 }, subagents: [] });
const saved = turn(1, [{ id: 1, kind: "tool", text: "", name: "save_doc", preview: "Saved draft (3 lines)." }]);
const chat = turn(2, [{ id: 2, kind: "tool", text: "", name: "get_doc", preview: "Title: Runbook" }]);
function api(routes: Record<string, () => [number, unknown]>) {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${url}`;
    if (!(key in routes)) throw new Error(`unexpected ${key}`);
    const [status, value] = routes[key]();
    return new Response(JSON.stringify(value), { status });
  }));
}
afterEach(() => vi.unstubAllGlobals());
const at = "2026-10-12T10:00:00Z";

test("savedIn counts only a successful save_doc or edit_doc", () => {
  expect(savedIn(saved)).toBe(true);
  expect(savedIn(chat)).toBe(false);
  expect(savedIn(turn(3, [{ id: 3, kind: "tool", text: "", name: "edit_doc", preview: "This text is not in the document.", isError: true }]))).toBe(false);
});

test("the Agent runs tab lists the runs with their drafts", async () => {
  api({ "GET /api/sessions?document_id=doc-1": () => [200, { sessions: [
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
  api({ "GET /api/sessions?document_id=doc-1": () => [200, { sessions: [] }] });
  render(<MemoryRouter><DocumentAgentRuns documentId="doc-1" /></MemoryRouter>);
  expect(await screen.findByText("No agent runs yet. Ask agent starts one.")).toBeInTheDocument();
});

test("a draft written by an Ask agent run links to its run page", () => {
  const base = { origin: "agent" as const, session_id: "s1", session_title: "Revise", author: null, document_id: "doc-1" };
  const { rerender } = render(<MemoryRouter><DraftAuthor draft={{ ...base, session_purpose: "revision" }} /></MemoryRouter>);
  expect(screen.getByRole("link", { name: "Agent run" })).toHaveAttribute("href", "/documents/doc-1/agent/s1");
  rerender(<MemoryRouter><DraftAuthor draft={{ ...base, session_purpose: "documents" }} /></MemoryRouter>);
  expect(screen.getByRole("link", { name: "Agent run" })).toHaveAttribute("href", "/sessions/s1");
});
