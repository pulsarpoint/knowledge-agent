import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import AgentChatView from "~/features/agents/AgentChatView";
import { RunRecordIndex } from "~/features/sessions/RunRecordPage";
import { Toaster } from "~/components/ui/sonner";
import type { SessionDetail } from "~/features/sessions/api";
import { agentOptions } from "../../lib/__tests__/agentScopeFixture";
vi.mock("../Markdown", () => ({ default: ({ content }: { content: string }) => <div>{content}</div> }));

class Stream extends EventTarget {
  static instances: Stream[] = [];
  close = vi.fn();
  constructor(public url: string) {
    super();
    Stream.instances.push(this);
  }
  emit(type: string, value: unknown) {
    this.dispatchEvent(new MessageEvent(type, { data: JSON.stringify(value) }));
  }
}
const at = "2026-10-28T10:00:00Z";
const base = {
  model_name: "GPT",
  model_id: "m1",
  status_message: "",
  input_tokens: 120,
  output_tokens: 30,
  cached_tokens: 0,
  model_calls: 1,
  reasoning_tokens: 0,
  draft: null,
  draft_turn: null,
  document_id: null,
  document_title: null,
  worker_active: true,
  queued: 0,
  given_resources: [],
  prompt_snapshot: { system_prompt: "You operate servers." },
  context_snapshot: {},
  inputs: [],
  tool_counts: [],
  tool_counts_through_id: 0,
  created_at: at,
  last_activity_at: at,
  closed_at: null,
};
const chat = {
  ...base,
  id: "r1",
  title: "Check disk",
  purpose: "chat",
  agent_id: "ops",
  agent_name: "Operations",
  status: "waiting",
  turns: 1,
  config: { id: "ops" },
} as unknown as SessionDetail;
const review = (id: string, title: string, turns = 0) =>
  ({
    ...base,
    id,
    title,
    purpose: "review",
    agent_id: "ops",
    agent_name: "Operations",
    status: "waiting",
    turns,
    config: { review_of: "cc:r1:1", reviewed_agent_id: "ops" },
  }) as unknown as SessionDetail;
const fresh = review("rv1", "Review: Check disk");
const earlier = review("rv0", "Review: Check disk (earlier)", 1);
const chatEvents = [
  { id: 1, turn: 1, seq: 1, kind: "input", payload: { task: "Check disk" } },
  { id: 2, turn: 1, seq: 2, kind: "message", payload: { text: "Disk is 40% full." } },
  { id: 3, turn: 1, seq: 3, kind: "end", payload: { status: "completed" } },
];
const reviewEvents = [
  { id: 11, turn: 1, seq: 1, kind: "input", payload: { task: "Be shorter" } },
  {
    id: 12,
    turn: 1,
    seq: 2,
    kind: "tool_call",
    payload: { name: "record_digest", summary: "the digest", call_id: "c1" },
  },
  { id: 13, turn: 1, seq: 3, kind: "tool_result", payload: { call_id: "c1", preview: "1 turn, 0 tool calls" } },
  { id: 14, turn: 1, seq: 4, kind: "message", payload: { text: "I propose a one-line answer rule." } },
  { id: 15, turn: 1, seq: 5, kind: "end", payload: { status: "completed" } },
];
const NO_MODEL = "Select an enabled model in this agent’s General tab.";
const BUSY = "Wait for the agent to finish before reviewing it.";
const NOT_REVIEWABLE = "Reviews are for a saved agent's chats and event runs.";
const KEY = "cc%3Ar1%3A1";

/** The API: agent "ops", its chat r1 (turn 1 recorded as cc:r1:1), its reviews and a review start (or a refusal). */
function serve({
  run = chat,
  reviews = [] as SessionDetail[],
  refuse = null as null | { status: number; message: string },
  failSend = false,
  record = { kind: "chat_session", key: "cc:r1:1" },
}: {
  run?: SessionDetail;
  reviews?: SessionDetail[];
  refuse?: null | { status: number; message: string };
  failSend?: boolean;
  record?: { kind: string; key: string };
} = {}) {
  const calls: { key: string; body: any }[] = [];
  const sessions: Record<string, SessionDetail> = { r1: run, rv1: fresh, rv0: earlier };
  const history: Record<string, unknown[]> = { r1: chatEvents, rv1: [], rv0: reviewEvents };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${url}`;
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      calls.push({ key, body });
      if (key === "GET /api/sessions/options?agent_id=ops") return Response.json(agentOptions);
      if (key === "GET /api/sessions?agent_id=ops") return Response.json({ sessions: [] });
      if (key === `GET /api/agent-runs/${encodeURIComponent(record.key)}`)
        return Response.json({
          run_key: record.key,
          agent_id: "ops",
          user: "admin@test",
          events: [
            {
              seq: 1,
              kind: "start",
              agent_id: "ops",
              payload: { kind: record.kind, title: "Check disk" },
              created_at: at,
            },
            { seq: 2, kind: "end", agent_id: "ops", payload: { status: "completed" }, created_at: at },
          ],
        });
      if (key === `GET /api/agent-runs/${KEY}/reviews`)
        return Response.json({ reviews: reviews.map((item) => ({ ...item, review_of: "cc:r1:1" })) });
      if (key === `POST /api/agent-runs/${KEY}/reviews`)
        return refuse
          ? Response.json({ error: { message: refuse.message } }, { status: refuse.status })
          : Response.json(fresh, { status: 201 });
      if (key === "POST /api/sessions/rv1/stop") return Response.json({ id: "rv1" }, { status: 202 });
      const messages = /^POST \/api\/sessions\/([^/]+)\/messages$/.exec(key);
      if (messages && failSend)
        return Response.json({ error: { message: "The message was not sent." } }, { status: 500 });
      if (messages) return Response.json({ seq: 1, status: "queued", working: true }, { status: 202 });
      const events = /^GET \/api\/sessions\/([^/]+)\/events\/history/.exec(key);
      if (events) return Response.json({ events: history[events[1]] ?? [], has_more: false });
      const detail = /^GET \/api\/sessions\/([^/?]+)$/.exec(key);
      if (detail && sessions[detail[1]]) return Response.json(sessions[detail[1]]);
      return Response.json({ error: { message: "Not found." } }, { status: 404 });
    }),
  );
  return calls;
}
const renderChatTab = () =>
  render(
    <MemoryRouter initialEntries={["/agents/ops/chat?session_id=r1"]}>
      <AgentChatView agentId="ops" agentName="Operations" available revision={3} />
      <Toaster />
    </MemoryRouter>,
  );
const renderRecord = (key = "cc:r1:1") =>
  render(
    <MemoryRouter initialEntries={[`/activity/runs/${encodeURIComponent(key)}`]}>
      <Routes>
        <Route path="/activity/runs/:runKey" element={<RunRecordIndex />} />
      </Routes>
      <Toaster />
    </MemoryRouter>,
  );
const improve = () => screen.findByRole("button", { name: "Improve agent" });
const openReview = async () => {
  const button = await improve();
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  return within(await screen.findByRole("dialog", { name: "Improve agent" }));
};

beforeEach(() => {
  vi.resetAllMocks();
  Stream.instances = [];
  vi.stubGlobal("EventSource", Stream);
  try {
    localStorage.clear();
    sessionStorage.clear();
  } catch {
    /* none */
  }
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

test("the agent chat: Improve agent on a finished session; the old feedback buttons are gone", async () => {
  serve();
  renderChatTab();
  expect(await screen.findByText("Disk is 40% full.")).toBeVisible();
  await waitFor(async () => expect(await improve()).toBeEnabled());
  expect(screen.queryByRole("button", { name: /Suggestions/ })).toBeNull();
  expect(screen.queryByRole("button", { name: "Add feedback" })).toBeNull();
});

test("the agent chat: Improve agent is disabled with the reason while the agent works", async () => {
  serve({ run: { ...chat, status: "working" } as SessionDetail });
  renderChatTab();
  expect(await improve()).toBeDisabled();
  // the reason is the button's hint, not a line of text beside it during every running turn
  expect(await improve()).toHaveAttribute("title", BUSY);
  expect(screen.queryByText(BUSY)).toBeNull();
});

test("the Sheet holds the shared chat; the first message starts the review and is sent as the feedback", async () => {
  const calls = serve();
  renderChatTab();
  const sheet = await openReview();
  expect(sheet.getByTestId("conversation-title")).toHaveTextContent("New review");
  // the shared agent chat: its Chat / Debug tabs and message box, with the feedback prompt
  expect(sheet.getByRole("tablist", { name: "Chat or debug" })).toBeInTheDocument();
  const box = sheet.getByRole("textbox", { name: "Message" });
  expect(box).toHaveAttribute("placeholder", "What should the agent do differently?");
  expect(sheet.queryByRole("button", { name: /^Settings/ })).toBeNull();
  expect(calls.some((c) => c.key === `POST /api/agent-runs/${KEY}/reviews`)).toBe(false); // nothing starts on open
  fireEvent.change(box, { target: { value: "Be shorter" } });
  await waitFor(() => expect(sheet.getByRole("button", { name: "Send message" })).toBeEnabled());
  fireEvent.click(sheet.getByRole("button", { name: "Send message" }));
  await waitFor(() =>
    expect(calls.find((c) => c.key === "POST /api/sessions/rv1/messages")?.body).toEqual({ text: "Be shorter" }),
  );
  const keys = calls.map((c) => c.key);
  expect(keys.indexOf(`POST /api/agent-runs/${KEY}/reviews`)).toBeLessThan(
    keys.indexOf("POST /api/sessions/rv1/messages"),
  );
  expect(calls.some((c) => c.key === "POST /api/sessions")).toBe(false); // never an ordinary chat
  expect(await screen.findByText("Review started.")).toBeInTheDocument();
  await waitFor(() => expect(box).toHaveValue(""));
});

test("no success toast when the feedback could not be sent", async () => {
  serve({ failSend: true });
  renderChatTab();
  const sheet = await openReview();
  fireEvent.change(sheet.getByRole("textbox", { name: "Message" }), { target: { value: "Be shorter" } });
  await waitFor(() => expect(sheet.getByRole("button", { name: "Send message" })).toBeEnabled());
  fireEvent.click(sheet.getByRole("button", { name: "Send message" }));
  expect(await sheet.findByRole("alert")).toHaveTextContent("The message was not sent.");
  expect(screen.queryByText("Review started.")).toBeNull();
});

test("Reviews lists earlier reviews; one reopens with its tool calls shown as in the agent chat", async () => {
  serve({ reviews: [earlier] });
  renderChatTab();
  const sheet = await openReview();
  fireEvent.click(sheet.getByRole("button", { name: "Reviews" }));
  const list = within(await screen.findByRole("list", { name: "Reviews" }));
  fireEvent.click(await list.findByRole("button", { name: /Review: Check disk \(earlier\)/ }));
  const reopened = within(await screen.findByRole("dialog", { name: "Improve agent" }));
  expect(await reopened.findByText("I propose a one-line answer rule.")).toBeVisible();
  // the tool call and its result, folded under the finished turn as in the agent chat
  expect(reopened.getByText("record_digest")).toBeInTheDocument();
  expect(reopened.getByText("1 turn, 0 tool calls")).toBeInTheDocument();
  expect(reopened.getByText("the digest")).toBeInTheDocument();
});

test("the reviewer's tool calls and results stream live, with Stop, as in the agent chat", async () => {
  const calls = serve();
  renderChatTab();
  const sheet = await openReview();
  fireEvent.change(sheet.getByRole("textbox", { name: "Message" }), { target: { value: "Be shorter" } });
  await waitFor(() => expect(sheet.getByRole("button", { name: "Send message" })).toBeEnabled());
  fireEvent.click(sheet.getByRole("button", { name: "Send message" }));
  await waitFor(() =>
    expect(Stream.instances.some((item) => item.url.startsWith("/api/sessions/rv1/events"))).toBe(true),
  );
  const stream = Stream.instances.find((item) => item.url.startsWith("/api/sessions/rv1/events"))!;
  act(() => {
    stream.emit("session", { status: "working", turns: 1 });
    stream.emit("run_event", { id: 21, turn: 1, seq: 1, kind: "input", payload: { task: "Be shorter" } });
    stream.emit("run_event", {
      id: 22,
      turn: 1,
      seq: 2,
      kind: "tool_call",
      payload: { name: "record_turn", summary: "turn 1", call_id: "c2" },
    });
    stream.emit("run_event", {
      id: 23,
      turn: 1,
      seq: 3,
      kind: "tool_result",
      payload: { call_id: "c2", preview: "Disk is 40% full." },
    });
  });
  // the agent chat's live Working block, with the call and its result in it
  expect(await sheet.findByText(/^Working · 1 tool call/)).toBeVisible();
  expect(sheet.getByText("record_turn")).toBeInTheDocument();
  expect(sheet.getByText("turn 1")).toBeInTheDocument();
  expect(sheet.getByText("Disk is 40% full.")).toBeInTheDocument();
  fireEvent.click(sheet.getByRole("button", { name: /^Stop/ }));
  await waitFor(() => expect(calls.map((c) => c.key)).toContain("POST /api/sessions/rv1/stop"));
});

test.each([
  [409, NO_MODEL],
  [409, BUSY],
  [400, NOT_REVIEWABLE],
])("a refused start (%s) shows its text in an Alert", async (status, message) => {
  serve({ refuse: { status, message } });
  renderChatTab();
  const sheet = await openReview();
  fireEvent.change(sheet.getByRole("textbox", { name: "Message" }), { target: { value: "Be shorter" } });
  await waitFor(() => expect(sheet.getByRole("button", { name: "Send message" })).toBeEnabled());
  fireEvent.click(sheet.getByRole("button", { name: "Send message" }));
  const alert = await sheet.findByRole("alert");
  expect(alert).toHaveTextContent(message);
});

test("the run record page: Improve agent opens the review Sheet for that run", async () => {
  const calls = serve();
  renderRecord();
  const sheet = await openReview();
  expect(sheet.getByRole("textbox", { name: "Message" })).toBeInTheDocument();
  await waitFor(() => expect(calls.map((c) => c.key)).toContain(`GET /api/agent-runs/${KEY}/reviews`));
});

test("the run record page: disabled with the reason while the session works", async () => {
  serve({ run: { ...chat, status: "working" } as SessionDetail });
  renderRecord();
  await waitFor(async () => expect(await improve()).toBeDisabled());
  expect(await screen.findByText(BUSY)).toBeInTheDocument();
});

test("the run record page: Improve agent enables when the working session finishes (polled every 5 s)", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    const run = { ...chat, status: "working" } as SessionDetail;
    const calls = serve({ run });
    renderRecord();
    await waitFor(async () => expect(await improve()).toBeDisabled());
    const loads = () => calls.filter((c) => c.key === "GET /api/sessions/r1").length;
    const before = loads();
    run.status = "waiting";
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    await waitFor(async () => expect(await improve()).toBeEnabled());
    expect(loads()).toBe(before + 1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15000);
    });
    expect(loads()).toBe(before + 1); // finished: no more polling
  } finally {
    vi.useRealTimers();
  }
});

test("the run record page: disabled with the reason without a saved agent", async () => {
  serve({ run: { ...chat, agent_id: null, purpose: "resource" } as unknown as SessionDetail });
  renderRecord();
  expect(await screen.findByText(NOT_REVIEWABLE)).toBeInTheDocument();
  expect(await improve()).toBeDisabled();
});

test("the run record page: a run outside a session has no Improve agent", async () => {
  serve({ record: { kind: "event_task", key: "task-1" } });
  renderRecord("task-1");
  expect(await screen.findByRole("heading", { name: "Check disk" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Improve agent" })).toBeNull();
  expect(screen.queryByText(NOT_REVIEWABLE)).toBeNull();
});

test("the old suggestion UI is gone", () => {
  const files = Object.keys(import.meta.glob("../../features/**/*.{ts,tsx}"));
  expect(files.some((file) => /RunSuggestions|SuggestionApply|agents\/reviews\.ts$/.test(file))).toBe(false);
  expect(files.some((file) => file.endsWith("sessions/reviews.ts"))).toBe(true);
});
