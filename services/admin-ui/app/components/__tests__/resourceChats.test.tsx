import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, MemoryRouter, RouterProvider, useLocation } from "react-router";
import routes from "~/routes";
import ResourceChats from "~/features/sessions/ResourceChats";
import ServicePage from "~/features/servers/ServicePage";
import EnvironmentPage from "~/features/servers/EnvironmentPage";
import SessionView from "~/features/sessions/SessionView";
import { resourceChatPath, resourceOf } from "~/features/sessions/api";
import { WidePageContext } from "~/lib/widePage";
import { options as shared } from "~/lib/__tests__/agentOptionsFixture";

vi.mock("../Markdown", () => ({ default: ({ content }: { content: string }) => <div>{content}</div> }));

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
  default: ({ heading, headerActions, empty, composer }: Record<string, any>) => (
    <div>
      {heading}
      {headerActions}
      {empty}
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

const at = "2026-10-14T10:00:00Z";
const run = (id: string, status: string, title: string, tags = ["services/v1"]) => ({
  id,
  title,
  status,
  purpose: "resource",
  tags,
  model_name: "GPT",
  started_by: null,
  created_at: at,
  last_activity_at: at,
  draft_id: null,
});
const detail = (id: string, status: string, tags = ["services/v1"]) => ({
  id,
  title: `Chat ${id}`,
  status,
  purpose: "resource",
  tags,
  server_id: null,
  agent_name: "Ad-hoc agent",
  model_name: "GPT",
  turns: 1,
  config: {},
  document_draft: null,
});

const RUNS = "GET /api/sessions?tag=services%2Fv1";
const base = (): Record<string, (body: any) => [number, unknown]> => ({
  "GET /api/sessions/options?service_id=v1": () => [200, options],
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
function Where() {
  const location = useLocation();
  return <output data-testid="where">{location.pathname + location.search}</output>;
}
function show(url = "/services/v1", actions?: React.ReactNode) {
  const router = createMemoryRouter(
    [
      {
        path: "/services/:serviceId",
        element: (
          <>
            <ResourceChats
              resource={{ type: "service", id: "v1", name: "payments" }}
              info={<p>The payments service.</p>}
              actions={actions}
            />
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

test("the page shows Info and the resource's chats; no chat until one is asked for", async () => {
  api({
    ...base(),
    [RUNS]: () => [
      200,
      {
        sessions: [
          run("r2", "waiting", "Disk usage"),
          run("r1", "closed", "Propose documents", ["services/v1", "proposals"]),
        ],
      },
    ],
  });
  show("/services/v1", <a href="/x">Proposals</a>);
  expect(screen.getByText("The payments service.")).toBeInTheDocument();
  const chats = await screen.findByRole("region", { name: "Chats" });
  expect(await within(chats).findByRole("button", { name: /Disk usage/ })).toHaveTextContent("Waiting for you");
  const wizard = within(chats).getByRole("button", { name: /Propose documents/ });
  expect(within(wizard).getByText("proposals")).toBeInTheDocument();
  expect(within(within(chats).getByRole("button", { name: /Disk usage/ })).queryByText("proposals")).toBeNull();
  expect(within(chats).getByRole("button", { name: "New chat" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Chat" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Proposals" })).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Ask agent" })).toBeNull();
  expect(screen.queryByRole("tab")).toBeNull();
  expect(wide.value).toBe(false);
  expect(live.requested.filter(Boolean)).toEqual([]);
});

test("clicking a chat opens it and puts it in the URL; at 2xl Info and chat are side by side, below tabs", async () => {
  live.sessions.r1 = detail("r1", "closed");
  api({ ...base(), [RUNS]: () => [200, { sessions: [run("r1", "closed", "Is it up?")] }] });
  show();
  fireEvent.click(await screen.findByRole("button", { name: /Is it up\?/ }));
  const pane = await screen.findByRole("region", { name: "Ask agent" });
  expect(await within(pane).findByRole("heading", { name: "Chat r1" })).toBeInTheDocument();
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/services/v1?chat=r1"));
  expect(wide.value).toBe(true);
  expect(screen.getByTestId("resource-layout")).toHaveClass("2xl:grid-cols-2");
  // below 2xl: tabs Info / Chat, the chat tab active once a chat opens
  const tabs = screen.getByRole("tablist", { name: "Info or chat" });
  expect(tabs).toHaveClass("2xl:hidden");
  expect(within(tabs).getByRole("tab", { name: "Chat" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByTestId("resource-info")).toHaveClass("hidden", "2xl:flex");
  expect(screen.getByTestId("resource-chat")).not.toHaveClass("hidden");
  await userEvent.click(within(tabs).getByRole("tab", { name: "Info" }));
  expect(screen.getByTestId("resource-info")).not.toHaveClass("hidden");
  expect(screen.getByTestId("resource-chat")).toHaveClass("hidden", "2xl:block");
  // Hide chat closes it and takes it out of the URL
  fireEvent.click(screen.getByRole("button", { name: "Hide chat" }));
  expect(screen.queryByRole("region", { name: "Ask agent" })).toBeNull();
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/services\/v1$/));
  expect(wide.value).toBe(false);
});

test("a ?chat= link opens that chat", async () => {
  live.sessions.r1 = detail("r1", "waiting");
  api({ ...base(), [RUNS]: () => [200, { sessions: [run("r2", "waiting", "Newer"), run("r1", "waiting", "Older")] }] });
  show("/services/v1?chat=r1");
  const pane = await screen.findByRole("region", { name: "Ask agent" });
  expect(await within(pane).findByRole("heading", { name: "Chat r1" })).toBeInTheDocument();
  expect(live.requested).not.toContain("r2");
  expect(screen.getByTestId("where")).toHaveTextContent("/services/v1?chat=r1");
});

test("a ?chat= of another resource's run is not opened on this page", async () => {
  live.sessions.x1 = detail("x1", "waiting", ["servers/s9"]);
  const calls = api({ ...base(), [RUNS]: () => [200, { sessions: [] }] });
  show("/services/v1?chat=x1");
  expect(await screen.findByText("This chat belongs to another page.")).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Chat x1" })).toBeNull();
  // nothing can be sent to it
  fireEvent.change(screen.getByLabelText("Message"), { target: { value: "hello" } });
  fireEvent.click(screen.getByRole("button", { name: "Send message" }));
  await new Promise((resolve) => setTimeout(resolve, 20));
  expect(calls.some((c) => c.key.startsWith("POST"))).toBe(false);
});

test("Chat opens the latest open chat", async () => {
  live.sessions.r2 = detail("r2", "waiting");
  api({ ...base(), [RUNS]: () => [200, { sessions: [run("r2", "waiting", "Open one")] }] });
  show();
  await screen.findByRole("button", { name: /Open one/ });
  fireEvent.click(screen.getByRole("button", { name: "Chat" }));
  expect(await screen.findByRole("heading", { name: "Chat r2" })).toBeInTheDocument();
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/services/v1?chat=r2"));
});

test("Chat never opens a Propose wizard run as the latest open chat", async () => {
  live.sessions.r3 = detail("r3", "working", ["services/v1", "proposals"]);
  live.sessions.r2 = detail("r2", "waiting");
  api({
    ...base(),
    [RUNS]: () => [
      200,
      {
        sessions: [
          run("r3", "working", "Propose documents for this service.", ["services/v1", "proposals"]),
          run("r2", "waiting", "Open one"),
        ],
      },
    ],
  });
  show();
  await screen.findByRole("button", { name: /Open one/ });
  fireEvent.click(screen.getByRole("button", { name: "Chat" }));
  expect(await screen.findByRole("heading", { name: "Chat r2" })).toBeInTheDocument();
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/services/v1?chat=r2"));
});

test("New chat starts a run from the resource that may not change content and opens it", async () => {
  live.sessions.r2 = detail("r2", "waiting");
  let listed = [run("r2", "waiting", "Open one")];
  const calls = api({
    ...base(),
    [RUNS]: () => [200, { sessions: listed }],
    "POST /api/sessions": () => {
      live.sessions.r9 = detail("r9", "starting");
      listed = [run("r9", "starting", "Is it up?"), ...listed];
      return [201, detail("r9", "starting")];
    },
  });
  show();
  const chats = await screen.findByRole("region", { name: "Chats" });
  await within(chats).findByRole("button", { name: /Open one/ });
  fireEvent.click(within(chats).getByRole("button", { name: "New chat" }));
  const pane = await screen.findByRole("region", { name: "Ask agent" });
  expect(within(pane).getByRole("heading", { name: "New conversation" })).toBeInTheDocument();
  expect(live.requested).not.toContain("r2"); // a new conversation, not the open chat
  await waitFor(() => expect(within(pane).getByRole("button", { name: "Settings" })).toBeEnabled());
  fireEvent.change(within(pane).getByLabelText("Message"), { target: { value: "Is it up?" } });
  fireEvent.click(within(pane).getByRole("button", { name: "Send message" }));
  await waitFor(() => expect(calls.some((c) => c.key === "POST /api/sessions")).toBe(true));
  const body = calls.find((c) => c.key === "POST /api/sessions")!.body;
  expect(body.started_from).toEqual({ service_id: "v1" });
  expect(body).not.toHaveProperty("content_editor");
  expect(body).not.toHaveProperty("tools");
  expect(body.resources.service_ids).toContain("v1");
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/services/v1?chat=r9"));
  expect(await within(chats).findByRole("button", { name: /Is it up\?/ })).toBeInTheDocument();
});

test("the service page shows the service's information in the service panel, and its chats", async () => {
  const service = {
    id: "v1",
    name: "payments",
    description: "Takes payments",
    service_type: "custom",
    service_role: "application",
    owner: "",
    address: null,
    revision: 1,
    created_at: at,
    updated_at: at,
    repositories: [],
    deployments: [],
    dependencies: [],
    dependents: [],
    environments: [],
  };
  api({
    ...base(),
    "GET /api/services/v1/context": () => [200, { service }],
    "GET /api/inventory/service/v1/documents": () => [200, { inventory_document: null, documents: [] }],
    "GET /api/services": () => [200, { services: [service] }],
    "GET /api/services/v1/deployments": () => [200, { deployments: [] }],
    "GET /api/services/v1/connections": () => [200, { connections: [] }],
    "GET /api/services/v1/relations": () => [200, { relations: [] }],
  });
  render(
    <MemoryRouter initialEntries={["/services/v1"]}>
      <ServicePage serviceId="v1" />
    </MemoryRouter>,
  );
  expect(await screen.findByRole("heading", { name: "payments", level: 1 })).toBeInTheDocument();
  expect(await screen.findByText("Takes payments")).toBeInTheDocument();
  const panel = within(screen.getByRole("region", { name: "payments" }));
  expect(panel.getByRole("region", { name: "Runs on" })).toHaveTextContent("No deployment recorded.");
  expect(panel.getByRole("region", { name: "Connections" })).toBeInTheDocument();
  expect(panel.getByRole("region", { name: "Relations" })).toBeInTheDocument();
  expect(panel.getByRole("region", { name: "Code projects" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Services" })).toHaveAttribute("href", "/services");
  expect(screen.getByRole("link", { name: "Proposals" })).toHaveAttribute("href", "/services/v1/proposals");
  // the documents are in their own tab, not in the panel
  expect(panel.queryByRole("tab", { name: "Documents" })).not.toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Documents" })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("tab", { name: "Documents" }));
  expect(await screen.findByRole("region", { name: "Documents" })).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "payments" })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("tab", { name: "Chats" }));
  expect(await screen.findByRole("region", { name: "Chats" })).toBeInTheDocument();
});

test("the service page keeps the agent context link, the code folder path and the SSH status of each deployment", async () => {
  const service = {
    id: "v1",
    name: "payments",
    description: "",
    service_type: "custom",
    service_role: "application",
    owner: "",
    address: null,
    revision: 1,
    created_at: at,
    updated_at: at,
    repositories: [
      {
        id: "r1",
        name: "payments-repo",
        remote_url: null,
        requested_ref: null,
        current_commit: null,
        checkout_path: "payments-repo",
        synced_at: null,
        archived: false,
      },
    ],
    deployments: [
      {
        id: "d1",
        server_id: "s1",
        environment_id: "e1",
        environment: "prod",
        runtime: "docker",
        runtime_name: "",
        port: null,
        log_location: "",
        deployed_version: "",
        server: {
          id: "s1",
          name: "app-1",
          hostname: "10.0.0.7",
          description: "",
          tags: [],
          ssh_allowed: true,
          access_enabled: true,
          allow_sudo: false,
        },
      },
    ],
    dependencies: [],
    dependents: [],
    environments: [],
  };
  api({
    ...base(),
    "GET /api/services/v1/context": () => [200, { service }],
    "GET /api/inventory/service/v1/documents": () => [200, { inventory_document: null, documents: [] }],
    "GET /api/services": () => [200, { services: [service] }],
    "GET /api/services/v1/deployments": () => [200, { deployments: [] }],
    "GET /api/services/v1/connections": () => [200, { connections: [] }],
    "GET /api/services/v1/relations": () => [200, { relations: [] }],
    "GET /api/storage": () => [200, { locations: [{ id: "code", container_path: "/code" }] }],
  });
  render(
    <MemoryRouter initialEntries={["/services/v1"]}>
      <ServicePage serviceId="v1" />
    </MemoryRouter>,
  );
  const extras = within(await screen.findByRole("region", { name: "Access and code" }));
  expect(extras.getByRole("link", { name: "Agent context (JSON)" })).toHaveAttribute(
    "href",
    "/api/services/v1/context",
  );
  expect(await extras.findByText("payments-repo: /code/payments-repo")).toBeInTheDocument();
  expect(extras.getByText("prod / app-1: SSH allowed: On · Connection: Enabled · Sudo: Disabled")).toBeInTheDocument();
});

test("the environment page shows its workspace of servers and services, and its chats", async () => {
  api({
    "GET /api/environments/e1": () => [
      200,
      {
        id: "e1",
        name: "Production",
        description: "Live",
        revision: 1,
        created_at: at,
        updated_at: at,
        services: [{ id: "v1", name: "payments" }],
        servers: [{ id: "s1", name: "web-1", hostname: "10.0.0.7" }],
      },
    ],
    "GET /api/environments/e1/model": () => [
      200,
      {
        environment: { id: "e1", name: "Production", description: "Live", revision: 1 },
        servers: [
          {
            id: "s1",
            name: "web-1",
            hostname: "10.0.0.7",
            description: "",
            tags: [],
            revision: 1,
            deployments: [
              {
                id: "d1",
                service_id: "v1",
                service_name: "payments",
                runtime: "docker",
                runtime_name: "payments",
                port: 8000,
                version: "",
                log_location: "",
                revision: 1,
              },
            ],
          },
        ],
        services: [],
        connections: [],
        relations: [],
        external: [],
        external_services: [],
      },
    ],
    "GET /api/inventory/environment/e1/documents": () => [200, { inventory_document: null, documents: [] }],
    "GET /api/sessions?tag=environments%2Fe1": () => [200, { sessions: [] }],
  });
  render(
    <MemoryRouter initialEntries={["/environments/e1?server=s1"]}>
      <EnvironmentPage environmentId="e1" />
    </MemoryRouter>,
  );
  expect(await screen.findByRole("heading", { name: "Production", level: 1 })).toBeInTheDocument();
  const servers = within(await screen.findByRole("region", { name: "Servers" }));
  expect(servers.getByRole("button", { name: /^web-1/ })).toHaveAttribute("aria-pressed", "true");
  const services = within(screen.getByRole("region", { name: "Services on web-1" }));
  expect(services.getByRole("button", { name: /^payments/ })).toBeInTheDocument();
  expect(services.getByRole("link", { name: "10.0.0.7" })).toHaveAttribute("href", "/servers/s1");
  expect(screen.queryByRole("region", { name: "Chats" })).not.toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Documents" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Chat" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Proposals" })).toHaveAttribute("href", "/environments/e1/proposals");
  await userEvent.click(screen.getByRole("tab", { name: "Chats" }));
  expect(await screen.findByText("No chats yet. Start one with Chat or New chat.")).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Servers" })).not.toBeInTheDocument();
});

test("the environment's Documents tab lists only the environment's own documents and is kept in the URL", async () => {
  const doc = (id: string, title: string, resource: { id: string; name: string }) => ({
    id,
    title,
    tags: [],
    description_short: "",
    resource: { kind: "environment", ...resource },
  });
  api({
    "GET /api/environments/e1": () => [
      200,
      {
        id: "e1",
        name: "Production",
        description: "",
        revision: 1,
        created_at: at,
        updated_at: at,
        services: [],
        servers: [],
      },
    ],
    "GET /api/inventory/environment/e1/documents": () => [
      200,
      {
        inventory_document: null,
        documents: [
          doc("x1", "Release process", { id: "e1", name: "Production" }),
          doc("x2", "Web host notes", { id: "s1", name: "web-1" }),
        ],
      },
    ],
    "GET /api/sessions?tag=environments%2Fe1": () => [200, { sessions: [] }],
  });
  let location = "";
  function Where() {
    location = useLocation().search;
    return null;
  }
  render(
    <MemoryRouter initialEntries={["/environments/e1?view=documents"]}>
      <EnvironmentPage environmentId="e1" />
      <Where />
    </MemoryRouter>,
  );
  expect(await screen.findByRole("link", { name: "Release process" })).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Web host notes" })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("tab", { name: "Workspace" }));
  expect(location).toBe("");
});

test("routes: resource pages and their proposals; the discovery run route is gone", () => {
  const paths = routes.map((item) => [item.path, item.file]);
  expect(paths).toEqual(
    expect.arrayContaining([
      ["servers/:serverId", "./routes/server.tsx"],
      ["services/:serviceId", "./routes/service.tsx"],
      ["environments/:environmentId", "./routes/environment.tsx"],
      ["servers/:serverId/proposals", "./routes/resourceProposals.tsx"],
      ["services/:serviceId/proposals", "./routes/resourceProposals.tsx"],
      ["environments/:environmentId/proposals", "./routes/resourceProposals.tsx"],
    ]),
  );
  expect(routes.find((item) => item.path === "servers/:serverId")!.children ?? []).toEqual([]);
  expect(JSON.stringify(routes)).not.toContain("discovery");
});

test("a resource chat's path and its resource from the tags", () => {
  expect(resourceChatPath({ type: "server", id: "s 1" }, "r1")).toBe("/servers/s%201?chat=r1");
  expect(resourceChatPath({ type: "environment", id: "e1" })).toBe("/environments/e1");
  expect(resourceOf(["proposals", "services/v1"])).toEqual({ type: "service", id: "v1" });
  expect(resourceOf(["proposals"])).toBeNull();
});

test("a resource chat's session page links to its resource page on that chat", async () => {
  const value = {
    ...detail("r1", "waiting", ["servers/s1"]),
    status_message: "",
    input_tokens: 0,
    output_tokens: 0,
    cached_tokens: 0,
    model_calls: 0,
    reasoning_tokens: 0,
    draft: null,
    draft_turn: null,
    document_id: null,
    worker_active: false,
    queued: 0,
    document_name: null,
    folder: null,
    agent_id: null,
    model_id: "m1",
    document_title: null,
    target_document_id: null,
    server_id: "s1",
    given_resources: [],
    prompt_snapshot: { system_prompt: "" },
    context_snapshot: {},
    tool_counts: [],
    inputs: [],
    created_at: at,
    last_activity_at: at,
    closed_at: null,
  };
  live.sessions.r1 = value;
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(value)),
  );
  render(
    <MemoryRouter>
      <SessionView sessionId="r1" />
    </MemoryRouter>,
  );
  expect(await screen.findByRole("link", { name: "the server" })).toHaveAttribute("href", "/servers/s1?chat=r1");
});

test("New chat while a ?chat= is open shows an empty new chat; the URL gets the new chat once it starts", async () => {
  live.sessions.r1 = detail("r1", "waiting");
  let listed = [run("r1", "waiting", "Old one")];
  const calls = api({
    ...base(),
    [RUNS]: () => [200, { sessions: listed }],
    "POST /api/sessions": () => {
      live.sessions.r9 = detail("r9", "starting");
      listed = [run("r9", "starting", "Fresh"), ...listed];
      return [201, detail("r9", "starting")];
    },
  });
  show("/services/v1?chat=r1");
  expect(await screen.findByRole("heading", { name: "Chat r1" })).toBeInTheDocument();
  const chats = screen.getByRole("region", { name: "Chats" });
  fireEvent.click(within(chats).getByRole("button", { name: "New chat" }));
  const pane = await screen.findByRole("region", { name: "Ask agent" });
  expect(await within(pane).findByRole("heading", { name: "New conversation" })).toBeInTheDocument();
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/services\/v1$/));
  // it stays a new chat: the old one never comes back
  await new Promise((resolve) => setTimeout(resolve, 30));
  expect(screen.queryByRole("heading", { name: "Chat r1" })).toBeNull();
  expect(screen.getByTestId("where")).toHaveTextContent(/^\/services\/v1$/);
  await waitFor(() => expect(within(pane).getByRole("button", { name: "Settings" })).toBeEnabled());
  fireEvent.change(within(pane).getByLabelText("Message"), { target: { value: "Hello" } });
  fireEvent.click(within(pane).getByRole("button", { name: "Send message" }));
  await waitFor(() => expect(calls.filter((c) => c.key === "POST /api/sessions")).toHaveLength(1));
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/services/v1?chat=r9"));
  expect(await screen.findByRole("heading", { name: "Chat r9" })).toBeInTheDocument();
  // the old chat still opens from the list
  fireEvent.click(within(chats).getByRole("button", { name: /Old one/ }));
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/services/v1?chat=r1"));
  expect(await screen.findByRole("heading", { name: "Chat r1" })).toBeInTheDocument();
});
