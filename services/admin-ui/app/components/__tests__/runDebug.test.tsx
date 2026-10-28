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
      properties: { errors_only: { anyOf: [{ type: "boolean" }, { type: "null" }], description: "Only errors." } },
      required: ["errors_only"],
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
            text: "Run cc:s1:1 digest",
            size_chars: 2000,
            full_chars: 50000,
            shortened_turns: [],
          },
          size_chars: 2100,
          approx_tokens: 525,
        });
      if (body.method === "timeline" && body.args.turn === 99)
        return json({ error: { message: "turn: there is no turn 99; the run has 3 turns." } }, 400);
      return json({
        result: { tool_calls: [{ call_id: "c1", name: "read_file", status: "ok" }], truncated: false },
        size_chars: 120,
        approx_tokens: 30,
      });
    }
    if (url.endsWith("/explore")) return json({ methods });
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
  expect(await screen.findByText(/Query the run with the session explorer/)).toBeInTheDocument();
});

test("the Debug page shows the digest ratio, runs a method and shows the tool schema", async () => {
  const posted: { method: string; args: Record<string, unknown> }[] = [];
  const fetchMock = stubFetch(posted);
  renderRoutes(routes, "/activity/runs/cc%3As1%3A1/debug");
  expect(await screen.findByText(/2,000 chars of 50,000 in the full run \(4%\)/)).toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([url]) => url === "/api/agent-runs/cc%3As1%3A1/explore")).toBe(true);

  await userEvent.click(screen.getByRole("combobox", { name: "Method" }));
  await userEvent.click(await screen.findByRole("option", { name: "timeline" }));
  expect(screen.getByText("The run events in order.")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("checkbox", { name: "tool_call" }));
  await userEvent.type(screen.getByRole("spinbutton", { name: "turn" }), "2");
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
  expect(await screen.findByText("Run cc:s1:1 digest")).toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: "Method" })).toHaveTextContent("digest");
});
