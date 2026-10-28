import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, MemoryRouter, Outlet, RouterProvider, useLocation } from "react-router";
import routes from "~/routes";
import ResourceChats from "~/features/sessions/ResourceChats";
import { environmentRoute } from "~/lib/__tests__/pageRoutes";
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

const RUNS = "GET /api/sessions?tag=services%2Fv1&purpose=resource";
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

/** The environment page at `url`, as routes.ts declares it; `rerender` renders it again. */
function showEnvironment(url: string) {
  const router = createMemoryRouter(
    [
      {
        element: (
          <>
            <Outlet />
            <Where />
          </>
        ),
        children: [environmentRoute],
      },
    ],
    { initialEntries: [url] },
  );
  // a new context value each render: the page renders again (the chat reads its session again)
  const view = () => (
    <WidePageContext.Provider value={() => {}}>
      <RouterProvider router={router} />
    </WidePageContext.Provider>
  );
  const { rerender, unmount } = render(view());
  return { router, unmount, rerender: () => rerender(view()) };
}
const where = () => screen.getByTestId("where").textContent;
const environmentSection = (name: string) =>
  within(screen.getByRole("navigation", { name: "Environment sections" })).getByRole("link", { name });

/** Opens the Chats sheet from the chat panel's header; returns its list. */
async function openConversations() {
  await userEvent.click(within(screen.getByTestId("resource-chat")).getByRole("button", { name: "Chats" }));
  const sheet = within(await screen.findByRole("dialog", { name: "Chats" }));
  return sheet;
}
const panelTitle = () => screen.getByTestId("conversation-title");

test("the page shows Info and Chat, no Chats list; no chat until one is asked for", async () => {
  api({
    ...base(),
    [RUNS]: () => [
      200,
      {
        sessions: [run("r2", "waiting", "Disk usage"), run("r1", "closed", "Discover this service.", ["services/v1"])],
      },
    ],
  });
  show("/services/v1");
  expect(screen.getByText("The payments service.")).toBeInTheDocument();
  await new Promise((resolve) => setTimeout(resolve, 20));
  expect(fetch).not.toHaveBeenCalled(); // the chats load when the chat panel opens
  expect(screen.queryByRole("region", { name: "Chats" })).toBeNull();
  expect(screen.queryByText(/Disk usage/)).toBeNull();
  expect(screen.queryByText(/No chats yet/)).toBeNull();
  // New chat is in the chat panel's header, not on the page
  expect(screen.queryByRole("button", { name: "New chat" })).toBeNull();
  expect(screen.getByRole("button", { name: "Chat" })).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Proposals" })).toBeNull();
  expect(screen.queryByRole("region", { name: "Ask agent" })).toBeNull();
  expect(screen.queryByRole("tab")).toBeNull();
  expect(wide.value).toBe(false);
  expect(live.requested.filter(Boolean)).toEqual([]);
});

test("Chats opens a side sheet listing the resource's chats with their status, the open one marked; picking one opens it", async () => {
  live.sessions.r1 = detail("r1", "closed");
  live.sessions.r2 = detail("r2", "waiting");
  api({
    ...base(),
    [RUNS]: () => [200, { sessions: [run("r2", "waiting", "Disk usage"), run("r1", "closed", "Is it up?")] }],
  });
  show();
  fireEvent.click(screen.getByRole("button", { name: "Chat" })); // the latest open chat
  expect(await screen.findByRole("heading", { name: "Chat r2" })).toBeInTheDocument();
  await waitFor(() => expect(panelTitle()).toHaveTextContent("Disk usage"));
  const menu = await openConversations();
  const items = within(menu.getByRole("list", { name: "Chats" })).getAllByRole("button");
  expect(items).toHaveLength(2);
  expect(items[0]).toHaveTextContent("Disk usage");
  expect(items[0]).toHaveTextContent("Waiting for you");
  expect(items[0]).toHaveAttribute("aria-current", "true");
  expect(items[1]).toHaveTextContent("Is it up?");
  expect(items[1]).toHaveTextContent("Finished");
  expect(items[1]).not.toHaveAttribute("aria-current");
  await userEvent.click(items[1]);
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "Chats" })).toBeNull());
  expect(await screen.findByRole("heading", { name: "Chat r1" })).toBeInTheDocument();
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/services/v1?chat=r1"));
});

test("Chat opens the panel and puts the chat in the URL; at 2xl Info and chat are side by side, below tabs", async () => {
  live.sessions.r1 = detail("r1", "waiting");
  api({ ...base(), [RUNS]: () => [200, { sessions: [run("r1", "waiting", "Is it up?")] }] });
  show();
  fireEvent.click(screen.getByRole("button", { name: "Chat" }));
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
  fireEvent.click(screen.getByRole("button", { name: "Chat" }));
  expect(await screen.findByRole("heading", { name: "Chat r2" })).toBeInTheDocument();
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/services/v1?chat=r2"));
});

test("New chat in the chat panel starts a new conversation from the resource and opens it", async () => {
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
  fireEvent.click(screen.getByRole("button", { name: "Chat" })); // the panel opens on the latest open chat
  expect(await screen.findByRole("heading", { name: "Chat r2" })).toBeInTheDocument();
  fireEvent.click(within(screen.getByTestId("resource-chat")).getByRole("button", { name: "New chat" }));
  const pane = await screen.findByRole("region", { name: "Ask agent" });
  expect(await within(pane).findByRole("heading", { name: "New chat" })).toBeInTheDocument();
  // the panel says New chat; Chats still lists the old one, not marked
  expect(panelTitle()).toHaveTextContent("New chat");
  const menu = await openConversations();
  const old = menu.getByRole("button", { name: /Open one/ });
  expect(old).not.toHaveAttribute("aria-current");
  await userEvent.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "Chats" })).toBeNull());
  await waitFor(() => expect(within(pane).getByRole("button", { name: "Settings" })).toBeEnabled());
  fireEvent.change(within(pane).getByLabelText("Message"), { target: { value: "Is it up?" } });
  fireEvent.click(within(pane).getByRole("button", { name: "Send message" }));
  await waitFor(() => expect(calls.some((c) => c.key === "POST /api/sessions")).toBe(true));
  const body = calls.find((c) => c.key === "POST /api/sessions")!.body;
  expect(body.started_from).toEqual({ service_id: "v1" });
  expect(body).not.toHaveProperty("tools");
  expect(body.resources.service_ids).toContain("v1");
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/services/v1?chat=r9"));
  await waitFor(() => expect(panelTitle()).toHaveTextContent("Is it up?"));
  const after = await openConversations();
  expect(after.getByRole("button", { name: /Is it up\?/ })).toHaveAttribute("aria-current", "true");
  expect(after.getByRole("button", { name: /Open one/ })).toBeInTheDocument();
});

test("with no chats yet the Chats sheet says so", async () => {
  api(base());
  show();
  fireEvent.click(screen.getByRole("button", { name: "Chat" }));
  const menu = await openConversations();
  expect(menu.getByText("No earlier chats.")).toBeInTheDocument();
  expect(menu.queryByRole("list", { name: "Chats" })).toBeNull();
});

test("the environment page shows its workspace of servers and services; no Chats section (?view=chats falls back)", async () => {
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
    "GET /api/sessions?tag=environments%2Fe1&purpose=resource": () => [200, { sessions: [] }],
  });
  showEnvironment("/environments/e1?server=s1&view=chats");
  expect(await screen.findByRole("heading", { name: "Production", level: 2 })).toBeInTheDocument();
  const servers = within(await screen.findByRole("region", { name: "Servers" }));
  expect(servers.getByRole("button", { name: /^web-1/ })).toHaveAttribute("aria-pressed", "true");
  const services = within(screen.getByRole("region", { name: "Services on web-1" }));
  expect(services.getByRole("button", { name: /^payments/ })).toBeInTheDocument();
  expect(services.getByRole("link", { name: "10.0.0.7" })).toHaveAttribute("href", "/servers/s1");
  expect(screen.queryByRole("region", { name: "Chats" })).not.toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Documents" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Chat" })).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Proposals" })).toBeNull();
  expect(screen.queryByRole("tab", { name: "Chats" })).toBeNull();
  const nav = within(screen.getByRole("navigation", { name: "Environment sections" }));
  expect(nav.getAllByRole("link").map((link) => link.textContent)).toEqual(["Workspace", "Documents"]);
  expect(environmentSection("Workspace")).toHaveAttribute("aria-current", "page");
  expect(where()).toBe("/environments/e1/workspace?server=s1");
  expect(
    screen.getByText(
      "The servers in this environment and the services deployed on them. Drafts wait here to be confirmed.",
    ),
  ).toBeVisible();
});

test("the environment header links its drafts to the workspace's Drafts filter", async () => {
  const environment = { id: "e1", name: "Production", description: "", status: "confirmed", revision: 1 };
  api({
    "GET /api/environments/e1": () => [
      200,
      { ...environment, created_at: at, updated_at: at, services: [], servers: [] },
    ],
    "GET /api/environments/e1/model": () => [
      200,
      {
        environment,
        servers: [],
        services: [],
        connections: [],
        relations: [],
        external: [],
        external_services: [],
        drafts: 2,
        document_drafts: 0,
      },
    ],
    "GET /api/inventory/environment/e1/documents": () => [200, { inventory_document: null, documents: [] }],
    "GET /api/sessions?tag=environments%2Fe1&purpose=resource": () => [200, { sessions: [] }],
  });
  showEnvironment("/environments/e1?view=documents");
  await waitFor(() => expect(where()).toBe("/environments/e1/documents"));
  expect(await screen.findByRole("link", { name: "2 drafts to review" })).toHaveAttribute(
    "href",
    "/environments/e1/workspace?drafts=only",
  );
});

test("the header counts document drafts: apart in the text, and alone they link to the Documents tab", async () => {
  const environment = { id: "e1", name: "Production", description: "", status: "confirmed", revision: 1 };
  let model = { drafts: 2, document_drafts: 1 };
  api({
    "GET /api/environments/e1": () => [
      200,
      { ...environment, created_at: at, updated_at: at, services: [], servers: [] },
    ],
    "GET /api/environments/e1/model": () => [
      200,
      {
        environment,
        servers: [],
        services: [],
        connections: [],
        relations: [],
        external: [],
        external_services: [],
        ...model,
      },
    ],
    "GET /api/inventory/environment/e1/documents": () => [200, { inventory_document: null, documents: [] }],
    "GET /api/sessions?tag=environments%2Fe1&purpose=resource": () => [200, { sessions: [] }],
  });
  const { unmount } = showEnvironment("/environments/e1");
  expect(await screen.findByRole("link", { name: "3 drafts to review (1 document)" })).toHaveAttribute(
    "href",
    "/environments/e1/workspace?drafts=only",
  );
  unmount();
  model = { drafts: 0, document_drafts: 2 };
  showEnvironment("/environments/e1");
  expect(await screen.findByRole("link", { name: "2 drafts to review" })).toHaveAttribute(
    "href",
    "/environments/e1/documents",
  );
});

test("the header link to the drafts keeps the open chat and goes to the workspace", async () => {
  const environment = { id: "e1", name: "Production", description: "", status: "confirmed", revision: 1 };
  live.sessions.r1 = detail("r1", "waiting", ["environments/e1"]);
  api({
    "GET /api/environments/e1": () => [
      200,
      { ...environment, created_at: at, updated_at: at, services: [], servers: [] },
    ],
    "GET /api/environments/e1/model": () => [
      200,
      {
        environment,
        servers: [],
        services: [],
        connections: [],
        relations: [],
        external: [],
        external_services: [],
        drafts: 1,
        document_drafts: 0,
      },
    ],
    "GET /api/inventory/environment/e1/documents": () => [200, { inventory_document: null, documents: [] }],
    "GET /api/sessions?tag=environments%2Fe1&purpose=resource": () => [
      200,
      { sessions: [run("r1", "waiting", "Chat r1", ["environments/e1"])] },
    ],
    "GET /api/sessions/options?environment_id=e1": () => [200, options],
  });
  showEnvironment("/environments/e1?view=documents&chat=r1");
  await waitFor(() => expect(where()).toBe("/environments/e1/documents?chat=r1"));
  expect(await screen.findByRole("link", { name: "1 draft to review" })).toHaveAttribute(
    "href",
    "/environments/e1/workspace?chat=r1&drafts=only",
  );
});

test("a turn of the page chat reloads the workspace and the drafts count (its agent may have written drafts)", async () => {
  const environment = { id: "e1", name: "Production", description: "", status: "confirmed", revision: 1 };
  live.sessions.r1 = detail("r1", "waiting", ["environments/e1"]);
  let drafts = 0;
  const calls = api({
    "GET /api/environments/e1": () => [
      200,
      { ...environment, created_at: at, updated_at: at, services: [], servers: [] },
    ],
    "GET /api/environments/e1/model": () => [
      200,
      {
        environment,
        servers: [],
        services: [],
        connections: [],
        relations: [],
        external: [],
        external_services: [],
        drafts,
        document_drafts: 0,
      },
    ],
    "GET /api/inventory/environment/e1/documents": () => [200, { inventory_document: null, documents: [] }],
    "GET /api/sessions?tag=environments%2Fe1&purpose=resource": () => [
      200,
      { sessions: [run("r1", "waiting", "Chat r1", ["environments/e1"])] },
    ],
    "GET /api/sessions/options?environment_id=e1": () => [200, options],
  });
  const { rerender } = showEnvironment("/environments/e1?chat=r1");
  expect(await screen.findByRole("heading", { name: "Chat r1" })).toBeInTheDocument();
  await screen.findByRole("region", { name: "Servers" });
  const models = () => calls.filter((call) => call.key === "GET /api/environments/e1/model").length;
  const before = models();
  // The agent saves drafts during its turn; the turn ends.
  drafts = 4;
  live.sessions.r1 = { ...detail("r1", "waiting", ["environments/e1"]), turns: 2 };
  rerender();
  expect(await screen.findByRole("link", { name: "4 drafts to review" })).toBeInTheDocument();
  // The header and the workspace both read the model again.
  await waitFor(() => expect(models()).toBeGreaterThanOrEqual(before + 2));
});

test("the environment's Documents sub-page lists only the environment's own documents", async () => {
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
    "GET /api/sessions?tag=environments%2Fe1&purpose=resource": () => [200, { sessions: [] }],
  });
  showEnvironment("/environments/e1/documents");
  expect(await screen.findByRole("link", { name: "Release process" })).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Web host notes" })).not.toBeInTheDocument();
  expect(
    screen.getByText("Document drafts waiting to be published, then the environment's own documents."),
  ).toBeVisible();
  await userEvent.click(environmentSection("Workspace"));
  await waitFor(() => expect(where()).toBe("/environments/e1/workspace"));
});

test("the environment's section links keep only the open chat, not the workspace filters", async () => {
  live.sessions.r1 = detail("r1", "waiting", ["environments/e1"]);
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
    "GET /api/inventory/environment/e1/documents": () => [200, { inventory_document: null, documents: [] }],
    "GET /api/sessions?tag=environments%2Fe1&purpose=resource": () => [
      200,
      { sessions: [run("r1", "waiting", "Chat r1", ["environments/e1"])] },
    ],
    "GET /api/sessions/options?environment_id=e1": () => [200, options],
  });
  showEnvironment("/environments/e1/workspace?server=s1&drafts=only&chat=r1");
  await screen.findByRole("heading", { name: "Production", level: 2 });
  expect(environmentSection("Documents")).toHaveAttribute("href", "/environments/e1/documents?chat=r1");
  expect(environmentSection("Workspace")).toHaveAttribute("href", "/environments/e1/workspace?chat=r1");
});

test.each([
  ["/environments/e1", "/environments/e1/workspace"],
  ["/environments/e1?view=workspace", "/environments/e1/workspace"],
  ["/environments/e1?view=documents", "/environments/e1/documents"],
  ["/environments/e1?view=documents&chat=r1", "/environments/e1/documents?chat=r1"],
  ["/environments/e1?server=s1&service=v1", "/environments/e1/workspace?server=s1&service=v1"],
])("an old environment link %s opens its sub-page, keeping the rest of the query", async (url, target) => {
  live.sessions.r1 = detail("r1", "waiting", ["environments/e1"]);
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
    "GET /api/inventory/environment/e1/documents": () => [200, { inventory_document: null, documents: [] }],
    "GET /api/sessions?tag=environments%2Fe1&purpose=resource": () => [
      200,
      { sessions: [run("r1", "waiting", "Chat r1", ["environments/e1"])] },
    ],
    "GET /api/sessions/options?environment_id=e1": () => [200, options],
  });
  showEnvironment(url);
  await waitFor(() => expect(where()).toBe(target));
  expect(await screen.findByRole("heading", { name: "Production", level: 2 })).toBeInTheDocument();
});

test("the header's document drafts link opens the Documents tab, where Drafts to review lists every counted draft", async () => {
  const environment = { id: "e1", name: "Production", description: "", status: "confirmed", revision: 1 };
  api({
    "GET /api/environments/e1": () => [
      200,
      { ...environment, created_at: at, updated_at: at, services: [], servers: [] },
    ],
    "GET /api/environments/e1/model": () => [
      200,
      {
        environment,
        servers: [],
        services: [],
        connections: [],
        relations: [],
        external: [],
        external_services: [],
        drafts: 0,
        document_drafts: 2,
      },
    ],
    "GET /api/environments/e1/document-drafts": () => [
      200,
      {
        drafts: [
          { id: "d1", title: "api tuning", new: true, resource: { type: "service", id: "v1", name: "api" } },
          { id: "d2", title: "web-1 runbook", new: false, resource: { type: "server", id: "s1", name: "web-1" } },
        ],
      },
    ],
    "GET /api/inventory/environment/e1/documents": () => [200, { inventory_document: null, documents: [] }],
    "GET /api/sessions?tag=environments%2Fe1&purpose=resource": () => [200, { sessions: [] }],
  });
  showEnvironment("/environments/e1");
  await userEvent.click(await screen.findByRole("link", { name: "2 drafts to review" }));
  const review = within(await screen.findByRole("region", { name: "Drafts to review" }));
  const items = review.getAllByRole("listitem");
  expect(items).toHaveLength(2);
  expect(within(items[0]).getByRole("link", { name: "api tuning" })).toHaveAttribute("href", "/documents/drafts/d1");
  expect(items[0]).toHaveTextContent("api");
  expect(items[0]).toHaveTextContent("New document");
  expect(within(items[1]).getByRole("link", { name: "web-1 runbook" })).toHaveAttribute("href", "/documents/drafts/d2");
  expect(items[1]).toHaveTextContent("web-1");
  expect(items[1]).toHaveTextContent("New version");
  // The environment's own documents stay below, own-only.
  expect(screen.getByText("No documents yet.")).toBeInTheDocument();
});

test("routes: resource pages; the proposals and discovery run routes are gone", () => {
  const paths = routes.map((item) => [item.path, item.file]);
  expect(paths).toEqual(
    expect.arrayContaining([
      ["servers/:serverId", "./routes/server.tsx"],
      ["services/:serviceId", "./routes/service.tsx"],
      ["environments/:environmentId", "./routes/environment.tsx"],
    ]),
  );
  expect(routes.find((item) => item.path === "environments/:environmentId")!.children).toEqual([
    expect.objectContaining({ index: true, file: "./routes/environmentIndex.tsx" }),
    expect.objectContaining({ path: "workspace", file: "./routes/environmentWorkspace.tsx" }),
    expect.objectContaining({ path: "documents", file: "./routes/environmentDocuments.tsx" }),
  ]);
  expect(JSON.stringify(routes)).not.toContain("discovery");
  expect(JSON.stringify(routes)).not.toContain("roposals");
});

test("a resource chat's path and its resource from the tags", () => {
  expect(resourceChatPath({ type: "server", id: "s 1" }, "r1")).toBe("/servers/s%201?chat=r1");
  expect(resourceChatPath({ type: "environment", id: "e1" })).toBe("/environments/e1");
  expect(resourceOf(["handoff", "services/v1"])).toEqual({ type: "service", id: "v1" });
  expect(resourceOf(["handoff"])).toBeNull();
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
  fireEvent.click(within(screen.getByTestId("resource-chat")).getByRole("button", { name: "New chat" }));
  const pane = await screen.findByRole("region", { name: "Ask agent" });
  expect(await within(pane).findByRole("heading", { name: "New chat" })).toBeInTheDocument();
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
  const menu = await openConversations();
  await userEvent.click(menu.getByRole("button", { name: /Old one/ }));
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/services/v1?chat=r1"));
  expect(await screen.findByRole("heading", { name: "Chat r1" })).toBeInTheDocument();
});
