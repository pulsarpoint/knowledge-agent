import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import AgentTestView from "~/features/agents/AgentTestView";
import * as reviews from "~/features/agents/reviews";
import type { RunSummary, SessionDetail } from "~/features/sessions/api";
import { agentOptions } from "../../lib/__tests__/agentScopeFixture";
vi.mock("~/features/agents/reviews");
vi.mock("../Markdown", () => ({ default: ({ content }: { content: string }) => <div>{content}</div> }));

class Stream extends EventTarget {
  static instances: Stream[] = [];
  close = vi.fn();
  constructor(public url: string) {
    super();
    Stream.instances.push(this);
  }
}
const at = "2026-10-13T10:00:00Z";
const finishedRun = {
  id: "r1",
  title: "Check disk",
  purpose: "test",
  agent_id: "ops",
  agent_name: "Operations",
  model_name: "GPT",
  model_id: "m1",
  status: "waiting",
  status_message: "",
  turns: 1,
  input_tokens: 120,
  output_tokens: 30,
  cached_tokens: 0,
  model_calls: 1,
  reasoning_tokens: 0,
  draft: null,
  draft_turn: null,
  document_id: null,
  document_title: null,
  document_name: null,
  folder: null,
  worker_active: true,
  queued: 0,
  given_resources: [],
  prompt_snapshot: { system_prompt: "You operate servers." },
  context_snapshot: {},
  inputs: [],
  tool_counts: [],
  tool_counts_through_id: 0,
  config: { id: "ops", tools: { items: [] } },
  reviews: [],
  created_at: at,
  last_activity_at: at,
  closed_at: null,
} as unknown as SessionDetail;
const closedRun = {
  ...finishedRun,
  id: "r0",
  title: "Earlier check",
  status: "closed",
  started_by: "admin@test",
  draft_id: null,
} as unknown as RunSummary;
const turn1 = [
  { id: 1, turn: 1, seq: 1, kind: "input", payload: { task: "Check disk" } },
  { id: 2, turn: 1, seq: 2, kind: "message", payload: { text: "Disk is 40% full." } },
  { id: 3, turn: 1, seq: 3, kind: "end", payload: { status: "completed" } },
];
const review = {
  id: "review-1",
  comment: "Be shorter",
  suggestion: "Answer in one line.",
  response_turn: 1,
  created_at: at,
};

/** The API for agent "ops": its options, its runs, a start and the run r1 (or another run). */
function serve({
  runs = [],
  run = finishedRun,
  options = () => agentOptions,
}: { runs?: RunSummary[]; run?: SessionDetail; options?: () => unknown } = {}) {
  const calls: { key: string; body: any }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${url}`;
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      calls.push({ key, body });
      if (key === "GET /api/sessions/options?agent_id=ops") return Response.json(options());
      if (key === "GET /api/sessions?agent_id=ops") return Response.json({ sessions: runs });
      if (key === "POST /api/sessions") return Response.json(run, { status: 201 });
      if (key === `POST /api/sessions/${run.id}/review`) return Response.json(review, { status: 201 });
      if (key === `POST /api/sessions/${run.id}/close`) return Response.json({ id: run.id });
      if (key === `POST /api/sessions/${run.id}/messages`)
        return Response.json({ seq: 2, status: "queued", working: false }, { status: 202 });
      if (url.startsWith(`/api/sessions/${run.id}/events/history`))
        return Response.json({ events: turn1, has_more: false });
      if (key === `GET /api/sessions/${run.id}`) return Response.json(run);
      return Response.json({ error: { message: "Not found." } }, { status: 404 });
    }),
  );
  return calls;
}
type Props = {
  available?: boolean;
  dirty?: boolean;
  sessionId?: string;
  revision?: number;
  onAgentChanged?: () => void;
};
function Page(props: Props) {
  const location = useLocation();
  return (
    <>
      <output aria-label="URL">{location.search}</output>
      <AgentTestView
        agentId="ops"
        agentName="Operations"
        available={props.available ?? true}
        dirty={props.dirty ?? false}
        active
        revision={props.revision ?? 3}
        onAgentChanged={props.onAgentChanged}
      />
    </>
  );
}
const renderTestTab = (props: Props = {}) =>
  render(
    <MemoryRouter initialEntries={[props.sessionId ? `?view=test&session_id=${props.sessionId}` : "?view=test"]}>
      <Page {...props} />
    </MemoryRouter>,
  );
const sendWhenReady = async (text: string) => {
  fireEvent.change(await screen.findByRole("textbox", { name: "Message" }), { target: { value: text } });
  await waitFor(() => expect(screen.getByRole("button", { name: "Send message" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Send message" }));
};
beforeEach(() => {
  vi.resetAllMocks();
  Stream.instances = [];
  vi.stubGlobal("EventSource", Stream);
  try {
    localStorage.clear();
  } catch {
    /* none */
  }
});
afterEach(() => vi.unstubAllGlobals());

test("the first message starts a run of the saved agent with the narrowed settings", async () => {
  const calls = serve();
  renderTestTab();
  expect(await screen.findByText("Send a message to test the agent.")).toBeVisible();
  await sendWhenReady("Check disk");
  await waitFor(() =>
    expect(calls.find((c) => c.key === "POST /api/sessions")?.body).toMatchObject({
      agent_id: "ops",
      message: "Check disk",
    }),
  );
  expect(calls.find((c) => c.key === "POST /api/sessions")?.body).not.toHaveProperty("system_prompt");
  await waitFor(() => expect(screen.getByLabelText("URL")).toHaveTextContent("session_id=r1"));
  expect(await screen.findByText("Disk is 40% full.")).toBeVisible();
  await sendWhenReady("And memory?");
  await waitFor(() =>
    expect(calls.find((c) => c.key === "POST /api/sessions/r1/messages")?.body).toEqual({ text: "And memory?" }),
  );
  expect(calls.filter((c) => c.key === "POST /api/sessions")).toHaveLength(1);
});

test("the test tab cannot start while the configuration tabs have unsaved changes", async () => {
  serve();
  renderTestTab({ dirty: true });
  expect(
    await screen.findByText("Save or discard changes in the configuration tabs before starting a task."),
  ).toBeInTheDocument();
});

test("the other cannot-start reason: a disabled agent", async () => {
  serve();
  renderTestTab({ available: false });
  expect(await screen.findByText("This agent is disabled. Enable it in General to run a task.")).toBeInTheDocument();
});

test("a saved agent with resources cannot start with none of them picked", async () => {
  const calls = serve();
  const none = { server_ids: [], service_ids: [], environment_ids: [], code_project_ids: [], collection_ids: [] };
  localStorage.setItem(
    "agent-settings:agent:ops",
    JSON.stringify({
      v: 2,
      revision: 3,
      linked: [],
      settings: { modelId: "m1", promptKey: "own", systemPrompt: "x", chosen: none, documents: [], off: [] },
    }),
  );
  renderTestTab();
  expect(await screen.findByText("Pick at least one resource")).toBeInTheDocument();
  fireEvent.change(screen.getByRole("textbox", { name: "Message" }), { target: { value: "Go" } });
  expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
  expect(calls.some((c) => c.key === "POST /api/sessions")).toBe(false);
});

test("feedback on an answer asks for suggestions and they can be reviewed and applied", async () => {
  vi.mocked(reviews.getReviewSource).mockReturnValue(new Promise(() => {}));
  const calls = serve({ run: finishedRun });
  renderTestTab({ sessionId: "r1" });
  expect(await screen.findByRole("button", { name: "Suggestions (0)" })).toBeDisabled();
  fireEvent.click(await screen.findByRole("button", { name: "Add feedback" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Your feedback" }), { target: { value: "Be shorter" } });
  fireEvent.click(screen.getByRole("button", { name: "Suggest improvements" }));
  await waitFor(() =>
    expect(calls.find((c) => c.key === "POST /api/sessions/r1/review")?.body).toEqual({
      comment: "Be shorter",
      turn: 1,
    }),
  );
  expect(await screen.findByText("Answer in one line.")).toBeVisible();
  expect(screen.getByRole("textbox", { name: "Your feedback" })).toHaveValue("");
  expect(screen.getByRole("button", { name: "Suggestions (1)", hidden: true })).toBeInTheDocument(); // behind the open sheet
  fireEvent.click(await screen.findByRole("button", { name: "Review and apply" }));
  expect(await screen.findByText("Apply suggestions")).toBeVisible();
  expect(reviews.getReviewSource).toHaveBeenCalledWith("ops", "review-1");
});

test("applying a suggestion to the agent says so and reloads the agent", async () => {
  const source = {
    revision: 3,
    instructions: "",
    changes: { instructions: "Answer in one line." },
    memory_notes: [],
  };
  vi.mocked(reviews.getReviewSource).mockResolvedValue(source as never);
  vi.mocked(reviews.applyReview).mockResolvedValue({ agent_id: "ops", saved: [], refused: [] } as never);
  serve({ run: { ...finishedRun, reviews: [review] } as SessionDetail });
  const onAgentChanged = vi.fn();
  renderTestTab({ sessionId: "r1", onAgentChanged });
  fireEvent.click(await screen.findByRole("button", { name: "Suggestions (1)" }));
  fireEvent.click(await screen.findByRole("button", { name: "Review and apply" }));
  const sheet = within(await screen.findByRole("dialog", { name: "Apply suggestions" }));
  await waitFor(() => expect(sheet.getByRole("button", { name: /this agent/i })).toBeEnabled());
  fireEvent.click(sheet.getByRole("button", { name: /this agent/i }));
  expect(await screen.findByText("Applied to this agent. New conversations use it.")).toBeVisible();
  expect(onAgentChanged).toHaveBeenCalled();
});

test("a saved suggestion lists the memory notes it proposes", async () => {
  const notes = [{ name: "disk/thresholds", content: "Alert at 90%.", metadata: {} }];
  serve({ run: { ...finishedRun, reviews: [{ ...review, memory_notes: notes }] } as SessionDetail });
  renderTestTab({ sessionId: "r1" });
  fireEvent.click(await screen.findByRole("button", { name: "Suggestions (1)" }));
  expect(await screen.findByText("Proposed memory notes: disk/thresholds")).toBeVisible();
});

test("earlier runs replace History", async () => {
  serve({ runs: [closedRun] });
  renderTestTab();
  await waitFor(() => expect(screen.getByRole("button", { name: "Earlier runs" })).toBeEnabled());
  expect(screen.queryByRole("button", { name: "History" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Show config" })).toBeNull();
});

test("an event run of this agent opens in the Test tab", async () => {
  serve({ run: { ...finishedRun, purpose: "event", title: "jira.ticket.created DEMO-1" } as SessionDetail });
  renderTestTab({ sessionId: "r1" });
  expect(await screen.findByText("Disk is 40% full.")).toBeVisible();
  expect(screen.queryByText("This run belongs to another agent.")).toBeNull();
});

test("a run of another agent is not opened here", async () => {
  serve({ run: { ...finishedRun, agent_id: "other" } as SessionDetail });
  renderTestTab({ sessionId: "r1" });
  expect(await screen.findByText("This run belongs to another agent.")).toBeVisible();
  expect(screen.queryByText("Disk is 40% full.")).toBeNull();
});

test("New chat closes the open run and starts an empty conversation", async () => {
  const calls = serve({ runs: [{ ...finishedRun, started_by: null, draft_id: null } as RunSummary] });
  renderTestTab({ sessionId: "r1" });
  await screen.findByText("Disk is 40% full.");
  fireEvent.click(screen.getByRole("button", { name: "New chat" }));
  await waitFor(() => expect(calls.map((c) => c.key)).toContain("POST /api/sessions/r1/close"));
  await waitFor(() => expect(screen.getByLabelText("URL")).not.toHaveTextContent("session_id"));
  expect(await screen.findByText("Send a message to test the agent.")).toBeVisible();
});

test("a new revision of the agent loads its options again and drops the remembered settings", async () => {
  const stored = "agent-settings:agent:ops";
  localStorage.setItem(
    stored,
    JSON.stringify({ v: 2, revision: 3, linked: [], settings: { chosen: {}, off: [], subagents: false, experts: [] } }),
  );
  let revision = 3;
  const calls = serve({ options: () => ({ ...agentOptions, agent: { ...agentOptions.agent!, revision } }) });
  const view = renderTestTab();
  await waitFor(() =>
    expect(JSON.parse(localStorage.getItem(stored)!)).toMatchObject({ revision: 3, settings: { experts: [] } }),
  );
  revision = 4;
  view.rerender(
    <MemoryRouter initialEntries={["?view=test"]}>
      <Page revision={4} />
    </MemoryRouter>,
  );
  await waitFor(() => expect(calls.filter((c) => c.key === "GET /api/sessions/options?agent_id=ops")).toHaveLength(2));
  await waitFor(() =>
    expect(JSON.parse(localStorage.getItem(stored)!)).toMatchObject({ revision: 4, settings: { experts: ["e1"] } }),
  );
  await sendWhenReady("Check disk");
  await waitFor(() =>
    expect(calls.find((c) => c.key === "POST /api/sessions")?.body).toMatchObject({
      agent_id: "ops",
      expert_ids: ["e1"],
    }),
  );
});
