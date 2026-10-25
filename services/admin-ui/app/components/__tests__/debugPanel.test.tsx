import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import DebugPanel from "~/features/sessions/DebugPanel";
import AgentChat from "~/features/sessions/AgentChat";
import { defaultSettings } from "~/features/sessions/agentSettings";
import { options as shared } from "~/lib/__tests__/agentOptionsFixture";

const live = vi.hoisted(() => ({ sessions: {} as Record<string, any> }));
vi.mock("~/features/sessions/useSessionEvents", async () => {
  const { useState } = await import("react");
  return {
    useSessionEvents: (id: string | null) => {
      const [error, setError] = useState("");
      return {
        session: id ? (live.sessions[id] ?? null) : null,
        setSession: vi.fn(),
        error,
        setError,
        events: [],
        older: "start",
        loadOlder: vi.fn(),
        toolCounts: undefined,
        reconnecting: false,
      };
    },
    useSessionNotice: () => ({ notice: null, clear: vi.fn(), stopping: vi.fn(), sent: vi.fn() }),
  };
});
vi.mock("~/features/sessions/RunWorkspace", () => ({
  default: ({ heading, empty }: Record<string, any>) => (
    <div data-testid="conversation">
      {heading}
      {empty}
    </div>
  ),
}));

const options = shared;
const view = {
  instructions: "You answer.\n\nResource: server s1 (web-1)",
  prompt: { id: "p1", name: "Server assistant", version: 2 },
  prompt_source: "library",
  agent_instructions: "",
  skills: [],
  tools: [
    { name: "ssh_run", description: "Run a read-only command on a server." },
    { name: "get_resource", description: "Read one resource." },
  ],
  access: {
    resources: [
      { type: "server", id: "s1", name: "web-1", description_short: "Shop front end", source: "given" },
      { type: "service", id: "n1", name: "nginx", description_short: "", source: "via server s1" },
      { type: "document", id: "d1", name: "Runbook", description_short: "", source: "given" },
    ],
  },
  model: { id: "m1", name: "GPT" },
};

function api(routes: Record<string, (body: any) => [number, unknown]>) {
  const calls: { key: string; body: any }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${url}`;
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      calls.push({ key, body });
      if (!(key in routes)) throw new Error(`unexpected ${key}`);
      const [status, value] = routes[key](body);
      return new Response(JSON.stringify(value), { status });
    }),
  );
  return calls;
}
afterEach(() => {
  vi.unstubAllGlobals();
  live.sessions = {};
});

test("an existing run: GET debug shows Instructions with Copy, Prompt, Tools, Resources, Roles and Model", async () => {
  const user = userEvent.setup();
  const calls = api({ "GET /api/sessions/r1/debug": () => [200, view] });
  const writeText = vi.fn(async () => undefined);
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  render(
    <MemoryRouter>
      <DebugPanel runId="r1" input={null} />
    </MemoryRouter>,
  );
  const instructions = await screen.findByRole("region", { name: "Instructions" });
  expect(within(instructions).getByText(/Resource: server s1 \(web-1\)/)).toBeInTheDocument();
  await user.click(within(instructions).getByRole("button", { name: "Copy" }));
  expect(writeText).toHaveBeenCalledWith(view.instructions);
  const prompt = screen.getByRole("region", { name: "Prompt" });
  expect(within(prompt).getByRole("link", { name: /Server assistant/ })).toHaveAttribute("href", "/prompts/p1");
  expect(within(prompt).getByText(/version 2/)).toBeInTheDocument();
  const tools = screen.getByRole("region", { name: "Tools" });
  expect(within(tools).getByText("ssh_run")).toBeInTheDocument();
  expect(within(tools).getByText("Run a read-only command on a server.")).toBeInTheDocument();
  expect(within(tools).getByText("get_resource")).toBeInTheDocument();
  const resources = screen.getByRole("region", { name: "Resources" });
  const rows = within(resources).getAllByRole("listitem");
  expect(rows.map((row) => row.textContent)).toEqual([
    "Serverweb-1Given",
    "ServicenginxVia server web-1", // a child names the resource it came with
    "DocumentRunbookGiven",
  ]);
  expect(screen.queryByRole("region", { name: "Roles" })).toBeNull(); // no roles: every run drafts within its access
  expect(within(screen.getByRole("region", { name: "Model" })).getByText("GPT")).toBeInTheDocument();
  expect(calls.map((call) => call.key)).toEqual(["GET /api/sessions/r1/debug"]);
});

test("a run with no resources says so", async () => {
  api({
    "GET /api/sessions/r1/debug": () => [200, { ...view, access: { resources: [] } }],
  });
  render(
    <MemoryRouter>
      <DebugPanel runId="r1" input={null} />
    </MemoryRouter>,
  );
  const resources = await screen.findByRole("region", { name: "Resources" });
  expect(within(resources).getByText("None.")).toBeInTheDocument();
});

test("what the start left out is said above the instructions", async () => {
  const note = "Server prod-1 is not given to this run: it has no usable SSH connection.";
  api({ "GET /api/sessions/r1/debug": () => [200, { ...view, notes: [note] }] });
  render(
    <MemoryRouter>
      <DebugPanel runId="r1" input={null} />
    </MemoryRouter>,
  );
  expect(await screen.findByText(note)).toBeInTheDocument();
});

test("the agent's instructions and skills are labelled", async () => {
  api({
    "GET /api/sessions/r1/debug": () => [
      200,
      {
        ...view,
        prompt: null,
        agent_instructions: "Be careful.",
        skills: [{ name: "Linux", folder: "skills/s1/abc123def456" }],
      },
    ],
  });
  render(
    <MemoryRouter>
      <DebugPanel runId="r1" input={null} />
    </MemoryRouter>,
  );
  const prompt = await screen.findByRole("region", { name: "Prompt" });
  expect(within(prompt).getByText("Agent instructions")).toBeInTheDocument();
  expect(within(prompt).getByText("Be careful.")).toBeInTheDocument();
  expect(within(prompt).getByText("Skills")).toBeInTheDocument();
  expect(within(prompt).getByText("Linux")).toBeInTheDocument();
  expect(within(prompt).getByText("abc123def456")).toBeInTheDocument(); // the folder's version
});

function chat(initialRunId: string | null) {
  const settings = {
    options,
    settings: defaultSettings(options),
    error: "",
    update: vi.fn(),
    replace: vi.fn(),
  };
  render(
    <MemoryRouter>
      <AgentChat
        scope={{ kind: "server", id: "s1", name: "web-1" }}
        settings={settings}
        initialRunId={initialRunId}
        fresh={initialRunId === null}
      />
    </MemoryRouter>,
  );
}

test("in a chat: Debug before the first message previews the next start", async () => {
  const calls = api({
    "GET /api/sessions?tag=servers%2Fs1": () => [200, { sessions: [] }],
    "POST /api/sessions/preview": () => [200, { config: {}, first_message: "(your next message)", ...view }],
  });
  chat(null);
  expect(screen.getByRole("tab", { name: "Conversation" })).toHaveAttribute("aria-selected", "true");
  expect(calls.some((call) => call.key === "POST /api/sessions/preview")).toBe(false); // only once Debug is open
  const debug = screen.getByRole("tab", { name: "Debug" });
  fireEvent.mouseDown(debug);
  fireEvent.click(debug);
  expect(await screen.findByRole("region", { name: "Instructions" })).toBeInTheDocument();
  expect(screen.getByTestId("conversation").parentElement).toHaveClass("hidden");
  const preview = calls.find((call) => call.key === "POST /api/sessions/preview")!.body;
  expect(preview.started_from).toEqual({ server_id: "s1" });
  expect(calls.some((call) => call.key.includes("/debug"))).toBe(false);
});

test("in a chat: Debug of an open run reads GET /api/sessions/{id}/debug", async () => {
  live.sessions.r1 = {
    id: "r1",
    title: "Disk",
    status: "waiting",
    purpose: "resource",
    tags: ["servers/s1"],
    turns: 1,
    config: {},
    document_draft: null,
  };
  const calls = api({
    "GET /api/sessions?tag=servers%2Fs1": () => [200, { sessions: [] }],
    "GET /api/sessions/r1/debug": () => [200, view],
  });
  chat("r1");
  const debug = screen.getByRole("tab", { name: "Debug" });
  fireEvent.mouseDown(debug);
  fireEvent.click(debug);
  expect(await screen.findByRole("region", { name: "Tools" })).toBeInTheDocument();
  await waitFor(() => expect(calls.some((call) => call.key === "GET /api/sessions/r1/debug")).toBe(true));
  expect(calls.some((call) => call.key === "POST /api/sessions/preview")).toBe(false);
});
