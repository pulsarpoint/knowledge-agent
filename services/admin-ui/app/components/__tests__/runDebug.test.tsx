import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderRoutes } from "~/lib/__tests__/dataRouter";
import RunRecordLayout, { RunDebugPage, RunRecordIndex } from "~/features/sessions/RunRecordPage";
import type { ExploreMethod } from "~/features/sessions/explore";

afterEach(() => vi.unstubAllGlobals());

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const methods: ExploreMethod[] = [
  {
    method: "overview",
    tool: "record_overview",
    description: "The run at a glance.",
    parameters: { type: "object", properties: {}, required: [], additionalProperties: false },
  },
  {
    method: "timeline",
    tool: "record_timeline",
    description: "The run events in order.",
    parameters: {
      type: "object",
      properties: {
        kinds: {
          anyOf: [
            { type: "array", items: { enum: ["input", "tool_call", "tool_result"], type: "string" } },
            { type: "null" },
          ],
          description: "Only these event kinds.",
        },
        turn: { anyOf: [{ type: "integer", minimum: 1 }, { type: "null" }], description: "Only this turn." },
        tool: { anyOf: [{ type: "string" }, { type: "null" }], description: "Only this tool." },
      },
      required: ["kinds", "turn", "tool"],
      additionalProperties: false,
    },
  },
  {
    method: "tool_calls",
    tool: "record_tool_calls",
    description: "The tool calls.",
    parameters: {
      type: "object",
      properties: {
        errors_only: { anyOf: [{ type: "boolean" }, { type: "null" }], description: "Only errors." },
        offset: { anyOf: [{ type: "integer", minimum: 0 }, { type: "null" }], description: "Where the page starts." },
      },
      required: ["errors_only", "offset"],
      additionalProperties: false,
    },
  },
  {
    method: "search",
    tool: "record_search",
    description: "Find text.",
    parameters: {
      type: "object",
      properties: { text: { type: "string", description: "The text." } },
      required: ["text"],
      additionalProperties: false,
    },
  },
  {
    method: "expert_call",
    tool: "record_expert_call",
    description: "One expert call.",
    parameters: {
      type: "object",
      properties: { call_id: { type: "string", description: "The call." } },
      required: ["call_id"],
      additionalProperties: false,
    },
  },
  {
    method: "tool_output",
    tool: "record_tool_output",
    description: "One page of one output.",
    parameters: {
      type: "object",
      properties: {
        call_id: { type: "string", description: "The call id." },
        offset: { anyOf: [{ type: "integer" }, { type: "null" }] },
      },
      required: ["call_id", "offset"],
      additionalProperties: false,
    },
  },
  {
    method: "digest",
    tool: "record_digest",
    description: "The whole run as text.",
    parameters: { type: "object", properties: {}, required: [], additionalProperties: false },
  },
];

const record = {
  run_key: "cc:s1:1",
  agent_id: "lead",
  user: null,
  tool_counts: [],
  events: [{ seq: 1, kind: "start", agent_id: "lead", payload: { title: "Plan the release" }, created_at: "" }],
};

function stubFetch(posted: { method: string; args: Record<string, unknown> }[] = []) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith("/explore") && init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      posted.push(body);
      if (body.method === "digest")
        return json({
          result: {
            format: "text",
            text: "Session s1 digest",
            size_chars: 2000,
            full_chars: 50000,
            focus_turn: 2,
            reduced: { "3": "line" },
            truncated: false,
          },
          size_chars: 2100,
          approx_tokens: 525,
        });
      if (body.method === "timeline" && body.args.turn === 99)
        return json({ error: { message: "turn: there is no turn 99; the session has turns 1, 2." } }, 400);
      if (body.method === "search")
        return json({
          result: { hits: [{ kind: "tool_result", call_id: "x1", in_call: "e1" }], next_offset: null },
          size_chars: 90,
          approx_tokens: 23,
        });
      const offset = Number(body.args.offset ?? 0);
      return json({
        result: {
          tool_calls: [{ call_id: offset ? "c2" : "c1", name: "read_file", status: "ok" }],
          next_offset: offset ? null : 1,
          truncated: false,
        },
        size_chars: 120,
        approx_tokens: 30,
      });
    }
    if (url.endsWith("/explore")) return json({ methods, session_id: "s1", turn: 2 });
    return json(record);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const routes = [
  {
    path: "/activity/runs/:runKey",
    element: <RunRecordLayout />,
    children: [
      { index: true, element: <RunRecordIndex /> },
      { path: "debug", element: <RunDebugPage /> },
    ],
  },
];

test("the run page has Record and Debug sub-pages with a way back", async () => {
  stubFetch();
  const { router } = renderRoutes(routes, "/activity/runs/cc%3As1%3A1");
  expect(screen.getByRole("link", { name: "All agent runs" })).toHaveAttribute("href", "/activity/runs");
  const nav = screen.getByRole("navigation", { name: "Run record sections" });
  expect(within(nav).getByRole("link", { name: "Record" })).toHaveAttribute("href", "/activity/runs/cc:s1:1");
  expect(await screen.findByText("Plan the release")).toBeInTheDocument();
  expect(screen.getByText(/Every event of the run/)).toBeInTheDocument();
  await userEvent.click(within(nav).getByRole("link", { name: "Debug" }));
  expect(router.state.location.pathname).toBe("/activity/runs/cc:s1:1/debug");
  expect(await screen.findByText(/Query the run's whole session with the session explorer/)).toBeInTheDocument();
});

test("the Debug page shows the digest ratio, runs a method and shows the tool schema", async () => {
  const posted: { method: string; args: Record<string, unknown> }[] = [];
  const fetchMock = stubFetch(posted);
  renderRoutes(routes, "/activity/runs/cc%3As1%3A1/debug");
  expect(await screen.findByText(/2,000 chars of 50,000 in the full session \(4%\)/)).toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([url]) => url === "/api/agent-runs/cc%3As1%3A1/explore")).toBe(true);

  await userEvent.click(screen.getByRole("combobox", { name: "Method" }));
  await userEvent.click(await screen.findByRole("option", { name: "timeline" }));
  expect(screen.getByText("The run events in order.")).toBeInTheDocument();
  expect(screen.getByText("Exploring session s1: every turn; this run is turn 2.")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("checkbox", { name: "tool_call" }));
  // the run's own turn is preselected
  expect(screen.getByRole("spinbutton", { name: "turn" })).toHaveValue(2);
  await userEvent.type(screen.getByRole("textbox", { name: "tool" }), "read_file");
  await userEvent.click(screen.getByRole("button", { name: "Run" }));
  await waitFor(() =>
    expect(posted.at(-1)).toEqual({ method: "timeline", args: { kinds: ["tool_call"], turn: 2, tool: "read_file" } }),
  );
  expect(await screen.findByText(/120 chars · ~30 tokens/)).toBeInTheDocument();

  await userEvent.click(screen.getByText("Tool schema the LLM sees"));
  expect(screen.getByText(/"name": "record_timeline"/)).toBeInTheDocument();
});

test("a validation error shows inline", async () => {
  stubFetch();
  renderRoutes(routes, "/activity/runs/cc%3As1%3A1/debug");
  await userEvent.click(await screen.findByRole("combobox", { name: "Method" }));
  await userEvent.click(await screen.findByRole("option", { name: "timeline" }));
  await userEvent.clear(screen.getByRole("spinbutton", { name: "turn" }));
  await userEvent.type(screen.getByRole("spinbutton", { name: "turn" }), "99");
  await userEvent.click(screen.getByRole("button", { name: "Run" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("turn: there is no turn 99");
});

test("a call id in a result fills the tool_output form", async () => {
  const posted: { method: string; args: Record<string, unknown> }[] = [];
  stubFetch(posted);
  renderRoutes(routes, "/activity/runs/cc%3As1%3A1/debug");
  await userEvent.click(await screen.findByRole("combobox", { name: "Method" }));
  await userEvent.click(await screen.findByRole("option", { name: "tool_calls" }));
  await userEvent.click(screen.getByRole("checkbox", { name: "errors_only" }));
  await userEvent.click(screen.getByRole("button", { name: "Run" }));
  await waitFor(() => expect(posted.at(-1)).toEqual({ method: "tool_calls", args: { errors_only: true } }));
  expect(await screen.findByRole("button", { name: /Next page/ })).toBeInTheDocument();
  await userEvent.click(await screen.findByRole("button", { name: "c1" }));
  expect(screen.getByRole("combobox", { name: "Method" })).toHaveTextContent("tool_output");
  expect(screen.getByRole("textbox", { name: "call_id" })).toHaveValue("c1");
  await userEvent.click(screen.getByRole("button", { name: "Run" }));
  await waitFor(() => expect(posted.at(-1)).toEqual({ method: "tool_output", args: { call_id: "c1" } }));
});

test("the digest result shows as text", async () => {
  stubFetch();
  renderRoutes(routes, "/activity/runs/cc%3As1%3A1/debug");
  await userEvent.click(await screen.findByRole("button", { name: "Show digest" }));
  expect(await screen.findByText("Session s1 digest")).toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: "Method" })).toHaveTextContent("digest");
});

test("Next page asks for the next offset", async () => {
  const posted: { method: string; args: Record<string, unknown> }[] = [];
  stubFetch(posted);
  renderRoutes(routes, "/activity/runs/cc%3As1%3A1/debug");
  await userEvent.click(await screen.findByRole("combobox", { name: "Method" }));
  await userEvent.click(await screen.findByRole("option", { name: "tool_calls" }));
  await userEvent.click(screen.getByRole("button", { name: "Run" }));
  await userEvent.click(await screen.findByRole("button", { name: "Next page (offset 1)" }));
  await waitFor(() => expect(posted.at(-1)).toEqual({ method: "tool_calls", args: { offset: 1 } }));
  expect(await screen.findByRole("button", { name: "c2" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Next page/ })).not.toBeInTheDocument();
  expect(screen.getByRole("spinbutton", { name: "offset" })).toHaveValue(1);
});

test("an in_call in a search hit opens that expert call", async () => {
  stubFetch();
  renderRoutes(routes, "/activity/runs/cc%3As1%3A1/debug");
  await userEvent.click(await screen.findByRole("combobox", { name: "Method" }));
  await userEvent.click(await screen.findByRole("option", { name: "search" }));
  await userEvent.type(screen.getByRole("textbox", { name: "text" }), "boom");
  await userEvent.click(screen.getByRole("button", { name: "Run" }));
  await userEvent.click(await screen.findByRole("button", { name: "e1" }));
  expect(screen.getByRole("combobox", { name: "Method" })).toHaveTextContent("expert_call");
  expect(screen.getByRole("textbox", { name: "call_id" })).toHaveValue("e1");
});

test("a failed digest says so", async () => {
  const fetchMock = stubFetch();
  const answer = fetchMock.getMockImplementation()!;
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) =>
    init?.method === "POST" && JSON.parse(String(init.body)).method === "digest"
      ? json({ error: { message: "Run record not found." } }, 404)
      : answer(url, init),
  );
  renderRoutes(routes, "/activity/runs/cc%3As1%3A1/debug");
  expect(await screen.findByText(/Digest unavailable: Run record not found/)).toBeInTheDocument();
});

test("the Record link keeps an expert call's ?call=", async () => {
  stubFetch();
  renderRoutes(routes, "/activity/runs/cc%3As1%3A1?call=e1");
  const nav = screen.getByRole("navigation", { name: "Run record sections" });
  expect(within(nav).getByRole("link", { name: "Record" })).toHaveAttribute("href", "/activity/runs/cc:s1:1?call=e1");
  expect(within(nav).getByRole("link", { name: "Debug" })).toHaveAttribute("href", "/activity/runs/cc:s1:1/debug");
});
