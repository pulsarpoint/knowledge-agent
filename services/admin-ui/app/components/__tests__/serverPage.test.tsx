import userEvent from "@testing-library/user-event";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, MemoryRouter, RouterProvider, useLocation } from "react-router";
import ServerPage from "~/features/servers/ServerPage";
import { WidePageContext } from "~/lib/widePage";
import { options as shared } from "~/lib/__tests__/agentOptionsFixture";

const options = shared;

const live = vi.hoisted(() => ({ sessions: {} as Record<string, any>, requested: [] as (string | null)[] }));
vi.mock("~/features/sessions/useSessionEvents", async () => {
  const { useState } = await import("react");
  return {
    useSessionEvents: (id: string | null) => {
      live.requested.push(id);
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
  default: ({ heading, headerActions, empty, composer, answerActions }: Record<string, any>) => (
    <div>
      {heading}
      {headerActions}
      {empty}
      <div data-testid="answer-actions">{answerActions({ turn: 1, work: [] })}</div>
      <label>
        Message
        <textarea
          placeholder={composer.placeholder}
          value={composer.value}
          onChange={(e) => composer.onChange(e.target.value)}
        />
      </label>
      {composer.blockedReason && <p>{composer.blockedReason}</p>}
      <button type="button" onClick={composer.onSend}>
        Send message
      </button>
    </div>
  ),
}));

const server = {
  id: "s1",
  name: "web-1",
  hostname: "10.0.0.7",
  ssh_port: 22,
  ssh_username: "ops",
  description: "Shop front end",
  tags: [],
  revision: 2,
  created_at: "",
  updated_at: "",
  access_enabled: true,
  allow_sudo: false,
  ssh_allowed: true,
};
const NOT_READY = "Set up SSH for this server first (SSH settings).";
const page = (ready: boolean, reason: string | null = null) => ({
  server,
  environments: [{ id: "e1", name: "Production" }],
  deployments: [
    {
      id: "d1",
      service_id: "n1",
      service_name: "nginx",
      environment: "Production",
      runtime: "systemd",
      runtime_name: "nginx",
      port: 80,
      deployed_version: "",
    },
  ],
  ssh_ready: reason !== NOT_READY,
  discovery: { ready, reason },
});
const at = "2026-10-11T10:00:00Z";
const run = (id: string, status: string, title = `Chat ${id}`) => ({
  id,
  title,
  status,
  purpose: "resource",
  tags: ["servers/s1"],
  model_name: "GPT",
  started_by: null,
  created_at: at,
  last_activity_at: at,
  draft_id: null,
  pending: 1,
  accepted: 0,
  rejected: 0,
});
const detail = (id: string, status: string, serverId = "s1") => ({
  id,
  title: `Chat ${id}`,
  status,
  purpose: "resource",
  tags: [`servers/${serverId}`],
  server_id: serverId,
  agent_name: "Ad-hoc agent",
  model_name: "GPT",
  turns: 1,
  config: {},
  document_draft: null,
});
const RUNS = "GET /api/sessions?tag=servers%2Fs1&purpose=resource";
const base = (ready = true, reason: string | null = null): Record<string, (body: any) => [number, unknown]> => ({
  "GET /api/servers/s1": () => [200, page(ready, reason)],
  "GET /api/inventory/server/s1/documents": () => [200, { inventory_document: null, documents: [] }],
  "GET /api/sessions/options?server_id=s1": () => [200, options],
  [RUNS]: () => [200, { sessions: [] }],
});
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
const NativeRequest = globalThis.Request;
beforeEach(() => {
  vi.stubGlobal(
    "Request",
    class extends NativeRequest {
      constructor(input: RequestInfo | URL, init?: RequestInit) {
        super(input, { ...init, signal: undefined });
      }
    },
  );
  try {
    localStorage.clear();
  } catch {
    /* none */
  }
});
afterEach(() => {
  vi.unstubAllGlobals();
  live.sessions = {};
  live.requested = [];
});

const wide = { value: false };
/** The location React rendered (the router's state may run ahead of it). */
function Where() {
  const location = useLocation();
  return <output data-testid="where">{location.pathname + location.search}</output>;
}
function show(url = "/servers/s1") {
  const router = createMemoryRouter(
    [
      {
        path: "/servers/:serverId",
        element: (
          <>
            <ServerPage serverId="s1" />
            <Where />
          </>
        ),
      },
    ],
    { initialEntries: [url] },
  );
  wide.value = false;
  render(
    <WidePageContext.Provider
      value={(on) => {
        wide.value = on;
      }}
    >
      <RouterProvider router={router} />
    </WidePageContext.Provider>,
  );
  return router;
}

test("shows the server, its services and documents; its chats are in the chat panel's Conversations sheet", async () => {
  api({
    ...base(),
    [RUNS]: () => [200, { sessions: [run("r1", "closed", "Disk usage")] }],
    "GET /api/inventory/server/s1/documents": () => [
      200,
      {
        inventory_document: null,
        documents: [
          {
            id: "doc1",
            title: "web-1 overview",
            tags: [],
            description_short: "",
            connection: "linked",
            resource: { type: "server", id: "s1", name: "web-1" },
          },
        ],
      },
    ],
  });
  show();
  expect(await screen.findByRole("heading", { name: "web-1", level: 2 })).toBeInTheDocument();
  expect(within(screen.getByRole("region", { name: "Services" })).getByText("Production")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "nginx" })).toHaveAttribute("href", "/services/n1/view");
  expect(screen.queryByRole("link", { name: "Proposals" })).toBeNull();
  await userEvent.click(screen.getByRole("tab", { name: "Documents" }));
  expect(await screen.findByRole("link", { name: "web-1 overview" })).toHaveAttribute("href", "/documents/doc1");
  expect(screen.queryByRole("region", { name: "Services" })).not.toBeInTheDocument();
  expect(screen.queryByText("No inventory document yet.")).toBeNull();
  expect(screen.getByRole("link", { name: "New document" })).toHaveAttribute(
    "href",
    "/documents/new?resource=server&resource_id=s1",
  );
  expect(screen.queryByRole("tab", { name: "Chats" })).toBeNull();
  expect(screen.queryByRole("region", { name: "Ask agent" })).toBeNull();
  // the server's chats are in the chat panel's Conversations sheet
  fireEvent.click(screen.getByRole("button", { name: "Chat" }));
  await userEvent.click(await screen.findByRole("button", { name: "Conversations" }));
  const sheet = within(await screen.findByRole("dialog", { name: "Conversations" }));
  expect(sheet.getByRole("button", { name: /Disk usage/ })).toHaveTextContent("Finished");
});

test("Chat opens a new conversation; the first message starts a run from the server that may not change content", async () => {
  const calls = api({
    ...base(),
    "POST /api/sessions": () => {
      live.sessions.r1 = detail("r1", "starting");
      return [201, detail("r1", "starting")];
    },
  });
  show();
  fireEvent.click(await screen.findByRole("button", { name: "Chat" }));
  const pane = await screen.findByRole("region", { name: "Ask agent" });
  expect(wide.value).toBe(true);
  expect(within(pane).getByText("Ask the agent about this server.")).toBeInTheDocument();
  await waitFor(() => expect(within(pane).getByRole("button", { name: "Settings" })).toBeEnabled());
  expect(within(pane).queryByText("No draft saved in this turn")).toBeNull();
  // the settings sheet offers no tool choices
  fireEvent.click(within(pane).getByRole("button", { name: "Settings" }));
  const sheet = await screen.findByRole("dialog");
  expect(within(sheet).queryByRole("checkbox", { name: "Save a document" })).toBeNull();
  fireEvent.keyDown(sheet, { key: "Escape" });
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  fireEvent.change(within(pane).getByLabelText("Message"), { target: { value: "Disk usage?" } });
  fireEvent.click(within(pane).getByRole("button", { name: "Send message" }));
  await waitFor(() => expect(calls.some((c) => c.key === "POST /api/sessions")).toBe(true));
  const body = calls.find((c) => c.key === "POST /api/sessions")!.body;
  expect(body.started_from).toEqual({ server_id: "s1" });
  expect(body).not.toHaveProperty("tools");
  expect(body.message).toBe("Disk usage?");
  expect(body.resources.server_ids).toContain("s1");
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/servers/s1?chat=r1"));
});

test("a ?chat= link opens the page with the chat on that run", async () => {
  live.sessions.r1 = detail("r1", "waiting");
  api({ ...base(), [RUNS]: () => [200, { sessions: [run("r2", "waiting"), run("r1", "waiting")] }] });
  show("/servers/s1?chat=r1");
  const pane = await screen.findByRole("region", { name: "Ask agent" });
  expect(await within(pane).findByRole("heading", { name: "Chat r1" })).toBeInTheDocument();
  expect(live.requested).not.toContain("r2");
});

test("a ?chat= of another server's run is not opened", async () => {
  live.sessions.r1 = detail("r1", "waiting", "s9");
  api(base());
  show("/servers/s1?chat=r1");
  expect(await screen.findByText("This chat belongs to another page.")).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Chat r1" })).toBeNull();
});

test("a server whose SSH is not ready shows its reason and blocks the first message", async () => {
  const calls = api(base(false, NOT_READY));
  show();
  expect(await screen.findByText(NOT_READY)).toBeInTheDocument();
  expect(screen.getByText("SSH not set up")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Chat" }));
  const pane = await screen.findByRole("region", { name: "Ask agent" });
  expect(within(pane).getByText(NOT_READY)).toBeInTheDocument();
  await waitFor(() => expect(within(pane).getByRole("button", { name: "Settings" })).toBeEnabled());
  fireEvent.change(within(pane).getByLabelText("Message"), { target: { value: "Disk usage?" } });
  fireEvent.click(within(pane).getByRole("button", { name: "Send message" }));
  await new Promise((resolve) => setTimeout(resolve, 20));
  expect(calls.some((c) => c.key === "POST /api/sessions")).toBe(false);
});

test("a missing server says so and links back to All servers", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) =>
      url.includes("/api/inventory")
        ? new Response(JSON.stringify({ documents: [] }))
        : new Response(JSON.stringify({ error: { message: "Server not found." } }), { status: 404 }),
    ),
  );
  show();
  expect(await screen.findByText("Server not found.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "All servers" })).toHaveAttribute("href", "/servers");
});

test("empty services and documents are explained", async () => {
  api({ ...base(), "GET /api/servers/s1": () => [200, { ...page(true), deployments: [] }] });
  show();
  expect(await screen.findByText("No services are recorded on this server.")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("tab", { name: "Documents" }));
  expect(await screen.findByText("No documents yet.")).toBeInTheDocument();
});

test("a draft server and a draft deployment show Draft; the services table confirms the deployment", async () => {
  const user = userEvent.setup();
  const drafted = page(true);
  let current = {
    ...drafted,
    server: { ...server, status: "draft" },
    deployments: [{ ...drafted.deployments[0], status: "draft", revision: 2 }],
  };
  const calls = api({
    ...base(),
    "GET /api/servers/s1": () => [200, current],
    "POST /api/inventory/deployment/d1/confirm?dry_run=1": () => [
      200,
      { confirmed: [{ kind: "deployment", id: "d1", name: "nginx on web-1 in Production" }] },
    ],
    "POST /api/inventory/deployment/d1/confirm": () => {
      current = { ...current, deployments: [{ ...current.deployments[0], status: "confirmed" }] };
      return [200, { confirmed: [] }];
    },
  });
  show();
  const table = within(await screen.findByRole("region", { name: "Services" }));
  const row = table.getByRole("link", { name: "nginx" }).closest("tr")!;
  expect(within(row).getByText("Draft")).toBeInTheDocument();
  expect(screen.getAllByText("Draft")).toHaveLength(2); // the server's own badge in the header
  await user.click(within(row).getByRole("button", { name: "Confirm nginx" }));
  await user.click(await screen.findByRole("button", { name: "Confirm" }));
  await waitFor(() => expect(within(row).queryByText("Draft")).not.toBeInTheDocument());
  expect(calls.find((call) => call.key === "POST /api/inventory/deployment/d1/confirm")!.body).toEqual({ revision: 2 });
});

test("a turn of the page chat reloads the server page (its agent may have written drafts)", async () => {
  live.sessions.r1 = detail("r1", "waiting");
  const calls = api({ ...base(), [RUNS]: () => [200, { sessions: [run("r1", "waiting")] }] });
  const view = () => (
    <MemoryRouter initialEntries={["/servers/s1?chat=r1"]}>
      <ServerPage serverId="s1" />
    </MemoryRouter>
  );
  const { rerender } = render(view());
  expect(await screen.findByRole("heading", { name: "Chat r1" })).toBeInTheDocument();
  const loads = () => calls.filter((call) => call.key === "GET /api/servers/s1").length;
  const before = loads();
  live.sessions.r1 = { ...detail("r1", "waiting"), turns: 2 };
  rerender(view());
  await waitFor(() => expect(loads()).toBe(before + 1));
});
