import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider, type RouteObject } from "react-router";
import routes from "~/routes";
import ServiceLayout from "~/features/servers/ServiceLayout";
import ServiceIndex from "~/features/servers/ServiceIndex";
import ServiceView from "~/features/servers/ServiceView";
import ServiceEdit, { ServiceNew } from "~/features/servers/ServiceEdit";
import ServiceCode from "~/features/servers/ServiceCode";
import { DependsOn, Parts, PartOf, UsedBy } from "~/features/servers/ServiceRelations";
import ServiceConnections from "~/features/servers/ServiceConnections";
import ServiceDocuments from "~/features/servers/ServiceDocuments";
import { resourceChatPath } from "~/features/sessions/api";
import { serviceAsset as asset, serviceDeployment, stubApi } from "~/lib/__tests__/serviceFixture";
import { options as shared } from "~/lib/__tests__/agentOptionsFixture";
import type { Connection, Deployment, Relation } from "~/features/servers/inventory";

vi.mock("~/components/Markdown", () => ({ default: ({ content }: { content: string }) => <div>{content}</div> }));
vi.mock("~/features/sessions/useSessionEvents", async () => {
  const { useState } = await import("react");
  return {
    useSessionEvents: () => {
      const [error, setError] = useState("");
      return {
        session: null,
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
        <textarea value={composer.value} onChange={(e) => composer.onChange(e.target.value)} />
      </label>
      <button type="button" onClick={composer.onSend}>
        Send message
      </button>
    </div>
  ),
}));

/** The service routes as routes.ts declares them. */
const serviceRoutes: RouteObject[] = [
  { path: "/services/new", element: <ServiceNew /> },
  {
    path: "/services/:serviceId",
    element: <ServiceLayout />,
    children: [
      { index: true, element: <ServiceIndex /> },
      { path: "view", element: <ServiceView /> },
      { path: "edit", element: <ServiceEdit /> },
      { path: "code", element: <ServiceCode /> },
      { path: "depends-on", element: <DependsOn /> },
      { path: "used-by", element: <UsedBy /> },
      { path: "part-of", element: <PartOf /> },
      { path: "parts", element: <Parts /> },
      { path: "connections", element: <ServiceConnections /> },
      { path: "documents", element: <ServiceDocuments /> },
    ],
  },
  { path: "/services", element: <p>All services list</p> },
];

function show(path: string) {
  // A data router's navigation builds a Request with jsdom's AbortSignal, which Node's Request refuses.
  const NativeRequest = globalThis.Request;
  vi.stubGlobal(
    "Request",
    class extends NativeRequest {
      constructor(input: RequestInfo | URL, init?: RequestInit) {
        super(input, { ...init, signal: undefined });
      }
    },
  );
  const router = createMemoryRouter(serviceRoutes, { initialEntries: [path] });
  render(<RouterProvider router={router} />);
  return router;
}

const repository = (id: string, name: string) => ({
  id,
  name,
  remote_url: `https://github.com/example/${name}.git`,
  requested_ref: null,
  current_commit: null,
  checkout_path: null,
  synced_at: null,
  archived: false,
});
const services = [
  asset("v1", "payments-api", [serviceDeployment("d1", "prod", "app-1"), serviceDeployment("d3", "staging", "app-2")], {
    description: "Takes payments",
    owner: "payments team",
    repositories: [repository("r1", "payments-repo")],
    environments: [
      { id: "e-prod", name: "prod" },
      { id: "e-staging", name: "staging" },
    ],
  }),
  asset("v2", "postgres", [serviceDeployment("d2", "prod", "db-1")]),
  asset("v3", "nginx", [serviceDeployment("d4", "prod", "app-1")]),
  asset("v4", "stripe", [], { service_type: "external", address: "api.stripe.com" }),
  asset("v5", "billing"),
];
const deployment = (id: string, server: string, environment: string): Deployment => ({
  id,
  service_id: "v1",
  server_id: server,
  environment_id: environment,
  runtime: "docker",
  runtime_name: id === "d1" ? "payments" : "",
  port: id === "d1" ? 8000 : null,
  log_location: "",
  deployed_version: "",
  status: "confirmed",
  revision: 2,
});
const deployments = [deployment("d1", "s-app-1", "e-prod"), deployment("d3", "s-app-2", "e-staging")];
const connection = (id: string, from: string, to: Partial<Connection>, port: number, protocol: string): Connection => ({
  id,
  from_deployment_id: from,
  to_deployment_id: null,
  to_service_id: null,
  to_host: null,
  port,
  protocol,
  description: "",
  status: "confirmed",
  revision: 3,
  ...to,
});
const connections = [
  connection("c1", "d1", { to_deployment_id: "d2" }, 5432, "tcp"),
  connection("c2", "d4", { to_deployment_id: "d1" }, 8000, "http"),
];
const relation = (id: string, from: string, kind: Relation["kind"], to: Partial<Relation>): Relation => ({
  id,
  from_service_id: from,
  kind,
  to_service_id: null,
  to_code_project_id: null,
  description: "",
  status: "confirmed",
  revision: 5,
  ...to,
});
const relations = [
  relation("rel1", "v1", "part_of", { to_service_id: "v5" }),
  relation("rel2", "v3", "depends_on", { to_service_id: "v1", description: "proxies to it" }),
  relation("rel3", "v1", "built_from", { to_code_project_id: "r1" }),
  relation("rel4", "v1", "depends_on", { to_service_id: "v2", description: "stores payments" }),
  relation("rel5", "v5", "part_of", { to_service_id: "v1" }),
];
const environments = [
  {
    id: "e-prod",
    name: "prod",
    servers: [
      { id: "s-app-1", name: "app-1" },
      { id: "s-db-1", name: "db-1" },
    ],
  },
  { id: "e-staging", name: "staging", servers: [{ id: "s-app-2", name: "app-2" }] },
];
const servers = [
  { id: "s-app-1", name: "app-1", hostname: "10.0.0.1" },
  { id: "s-app-2", name: "app-2", hostname: "10.0.0.2" },
  { id: "s-db-1", name: "db-1", hostname: "10.0.0.3" },
  { id: "s-new", name: "new-1", hostname: "10.0.0.9" },
];
type Route = (body: any) => [number, unknown];
function api(extra: Record<string, Route> = {}, list = services, rels = relations) {
  return stubApi({
    "GET /api/services": () => [200, { services: list }],
    "GET /api/services/v1/deployments": () => [200, { deployments }],
    "GET /api/services/v1/connections": () => [200, { connections }],
    "GET /api/services/v1/relations": () => [200, { relations: rels }],
    "GET /api/sessions/options?service_id=v1": () => [200, { ...shared, discovery_prompts: [] }],
    "GET /api/inventory/service/v1/documents": () => [200, { inventory_document: null, documents: [] }],
    "GET /api/repositories": () => [
      200,
      { repositories: [repository("r1", "payments-repo"), repository("r2", "shared-lib")] },
    ],
    "GET /api/storage": () => [200, { locations: [] }],
    "GET /api/sessions?tag=services%2Fv1": () => [200, { sessions: [] }],
    "GET /api/environments": () => [200, { environments }],
    "GET /api/servers": () => [200, { servers }],
    "POST /api/relations": (body) => [201, { ...body, id: "rel-new", status: "confirmed", revision: 1 }],
    ...extra,
  });
}
const nav = () => within(screen.getByRole("navigation", { name: "Service sections" }));
const writes = (calls: { key: string; body: any }[]) =>
  calls.filter((call) => !call.key.startsWith("GET ")).map((call) => [call.key, call.body]);

afterEach(() => vi.unstubAllGlobals());

test("the routes: a service page with one sub-page per tab, and /services/new", () => {
  const children = (path: string) =>
    routes.find((item) => item.path === path)?.children?.map((item) => (item.index ? "(index)" : item.path));
  expect(children("services/:serviceId")).toEqual([
    "(index)",
    "view",
    "edit",
    "code",
    "depends-on",
    "used-by",
    "part-of",
    "parts",
    "connections",
    "documents",
  ]);
  expect(routes.some((item) => item.path === "services/new")).toBe(true);
  expect(resourceChatPath({ type: "service", id: "v 1" }, "r1")).toBe("/services/v%201/view?chat=r1");
});

test("an old link opens View; the header has the name, badges, Chat, and tabs with counts", async () => {
  api();
  const router = show("/services/v1");
  await waitFor(() => expect(router.state.location.pathname).toBe("/services/v1/view"));
  expect(await screen.findByRole("heading", { name: "payments-api" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "All services" })).toHaveAttribute("href", "/services");
  expect(screen.getByRole("button", { name: "Chat" })).toBeInTheDocument();
  const header = within(screen.getByTestId("service-header"));
  expect(header.getByText("Custom application")).toBeInTheDocument();
  expect(header.getByText("Application")).toBeInTheDocument();
  const tabs: [string, string][] = [
    ["View", "view"],
    ["Edit", "edit"],
    ["Code (1)", "code"],
    ["Depends on (1)", "depends-on"],
    ["Used by (1)", "used-by"],
    ["Part of (1)", "part-of"],
    ["Parts (1)", "parts"],
    ["Connections (2)", "connections"],
    ["Documents", "documents"],
  ];
  for (const [name, page] of tabs)
    expect(nav().getByRole("link", { name })).toHaveAttribute("href", `/services/v1/${page}`);
  await waitFor(() => expect(nav().getByRole("link", { name: "View" })).toHaveAttribute("aria-current", "page"));
});

test("an old ?view=documents link opens Documents and keeps the chat", async () => {
  api();
  const router = show("/services/v1?view=documents&chat=r1");
  await waitFor(() => expect(router.state.location.pathname).toBe("/services/v1/documents"));
  expect(router.state.location.search).toBe("?chat=r1");
});

test("View: description, owner, where it runs with Edit and Remove per deployment, and counts linking to the tabs", async () => {
  const { calls } = { calls: api({ "DELETE /api/deployments/d3?revision=2": () => [200, { id: "d3" }] }) };
  show("/services/v1/view");
  expect(await screen.findByText("Takes payments")).toBeInTheDocument();
  expect(screen.getByText("payments team")).toBeInTheDocument();
  const runs = within(screen.getByRole("region", { name: "Where it runs" }));
  expect(runs.getByRole("link", { name: "prod" })).toHaveAttribute("href", "/environments/e-prod");
  expect(runs.getByText(/prod \/ app-1 · docker · payments · :8000/)).toBeInTheDocument();
  expect(runs.getByRole("button", { name: "Edit prod / app-1" })).toBeInTheDocument();
  const related = within(screen.getByRole("region", { name: "Related" }));
  expect(related.getByRole("link", { name: /Depends on 1/ })).toHaveAttribute("href", "/services/v1/depends-on");
  expect(related.getByRole("link", { name: /Code projects 1/ })).toHaveAttribute("href", "/services/v1/code");
  await userEvent.click(runs.getByRole("button", { name: "Remove staging / app-2" }));
  await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Remove" }));
  await waitFor(() => expect(writes(calls)).toEqual([["DELETE /api/deployments/d3?revision=2", null]]));
});

test("View keeps the agent context link, each code project's folder and each deployment's SSH status", async () => {
  const list = services.map((item) =>
    item.id === "v1"
      ? { ...item, repositories: [{ ...repository("r1", "payments-repo"), checkout_path: "pay" }] }
      : item,
  );
  api({ "GET /api/storage": () => [200, { locations: [{ id: "code", container_path: "/code" }] }] }, list);
  show("/services/v1/view");
  const extras = within(await screen.findByRole("region", { name: "Access and code" }));
  expect(extras.getByRole("link", { name: "Agent context (JSON)" })).toHaveAttribute(
    "href",
    "/api/services/v1/context",
  );
  expect(await extras.findByText("payments-repo: /code/pay")).toBeInTheDocument();
  expect(extras.getByText(/^prod \/ app-1: SSH allowed: Off/)).toBeInTheDocument();
});

test("Depends on lists the services it depends on with their descriptions; Remove asks first", async () => {
  const calls = api({ "DELETE /api/relations/rel4?revision=5": () => [200, { id: "rel4" }] });
  show("/services/v1/depends-on");
  const list = within(await screen.findByRole("region", { name: "Depends on" }));
  expect(list.getByRole("link", { name: "postgres" })).toHaveAttribute("href", "/services/v2/view");
  expect(list.getByText("stores payments")).toBeInTheDocument();
  expect(list.queryByText("billing")).toBeNull();
  await userEvent.click(list.getByRole("button", { name: "Remove postgres" }));
  const dialog = within(screen.getByRole("alertdialog"));
  expect(dialog.getByText("Remove “payments-api depends on postgres”?")).toBeInTheDocument();
  await userEvent.click(dialog.getByRole("button", { name: "Cancel" }));
  expect(writes(calls)).toEqual([]);
  await userEvent.click(list.getByRole("button", { name: "Remove postgres" }));
  await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Remove" }));
  await waitFor(() => expect(writes(calls)).toEqual([["DELETE /api/relations/rel4?revision=5", null]]));
});

test("Depends on: Add picks a service in a sheet with search and creates depends_on from this service", async () => {
  const calls = api();
  show("/services/v1/depends-on");
  await userEvent.click(await screen.findByRole("button", { name: "Add" }));
  const sheet = within(await screen.findByRole("dialog", { name: "What payments-api depends on" }));
  // Not itself, and not one already related this way.
  expect(sheet.queryByRole("button", { name: "payments-api" })).toBeNull();
  expect(sheet.queryByRole("button", { name: "postgres" })).toBeNull();
  await userEvent.type(sheet.getByRole("searchbox", { name: "Search services" }), "str");
  expect(sheet.queryByRole("button", { name: "nginx" })).toBeNull();
  await userEvent.click(sheet.getByRole("button", { name: "stripe" }));
  await userEvent.type(sheet.getByLabelText("Description (optional)"), "charges cards");
  await userEvent.click(sheet.getByRole("button", { name: "Add" }));
  await waitFor(() =>
    expect(writes(calls)).toEqual([
      [
        "POST /api/relations",
        {
          from_service_id: "v1",
          kind: "depends_on",
          to_service_id: "v4",
          to_code_project_id: null,
          description: "charges cards",
        },
      ],
    ]),
  );
});

test("Used by lists the services that depend on this one; Add creates depends_on from the chosen service to this one", async () => {
  const calls = api();
  show("/services/v1/used-by");
  const list = within(await screen.findByRole("region", { name: "Used by" }));
  expect(list.getByRole("link", { name: "nginx" })).toHaveAttribute("href", "/services/v3/view");
  expect(list.getByText("proxies to it")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Add" }));
  const sheet = within(await screen.findByRole("dialog", { name: "What depends on payments-api" }));
  expect(sheet.queryByRole("button", { name: "nginx" })).toBeNull();
  await userEvent.click(sheet.getByRole("button", { name: "billing" }));
  await userEvent.click(sheet.getByRole("button", { name: "Add" }));
  await waitFor(() =>
    expect(writes(calls)).toEqual([
      [
        "POST /api/relations",
        { from_service_id: "v5", kind: "depends_on", to_service_id: "v1", to_code_project_id: null, description: "" },
      ],
    ]),
  );
});

test("Part of lists what this service is part of", async () => {
  api();
  show("/services/v1/part-of");
  const list = within(await screen.findByRole("region", { name: "Part of" }));
  expect(list.getByRole("link", { name: "billing" })).toHaveAttribute("href", "/services/v5/view");
  expect(list.getAllByRole("listitem")).toHaveLength(1);
});

test("Parts lists its parts; Add creates part_of from the chosen service to this one", async () => {
  const calls = api();
  show("/services/v1/parts");
  const list = within(await screen.findByRole("region", { name: "Parts" }));
  expect(list.getByRole("link", { name: "billing" })).toHaveAttribute("href", "/services/v5/view");
  await userEvent.click(screen.getByRole("button", { name: "Add" }));
  const sheet = within(await screen.findByRole("dialog", { name: "What is part of payments-api" }));
  await userEvent.click(sheet.getByRole("button", { name: "postgres" }));
  await userEvent.click(sheet.getByRole("button", { name: "Add" }));
  await waitFor(() =>
    expect(writes(calls)).toEqual([
      [
        "POST /api/relations",
        { from_service_id: "v2", kind: "part_of", to_service_id: "v1", to_code_project_id: null, description: "" },
      ],
    ]),
  );
});

test("a draft relation shows the Draft badge with Confirm and Discard", async () => {
  api({}, services, [relation("rel4", "v1", "depends_on", { to_service_id: "v2", status: "draft" })]);
  show("/services/v1/depends-on");
  const list = within(await screen.findByRole("region", { name: "Depends on" }));
  expect(list.getByText("Draft")).toBeInTheDocument();
  expect(list.getByRole("button", { name: "Confirm postgres" })).toBeInTheDocument();
  expect(list.getByRole("button", { name: "Discard postgres" })).toBeInTheDocument();
});

test("Code lists the code projects it is built from; Add picks one in a sheet; Remove asks first", async () => {
  const calls = api({ "DELETE /api/relations/rel3?revision=5": () => [200, { id: "rel3" }] });
  show("/services/v1/code");
  const list = within(await screen.findByRole("region", { name: "Built from" }));
  expect(list.getByRole("link", { name: "payments-repo" })).toHaveAttribute("href", "/code-projects/r1");
  await userEvent.click(screen.getByRole("button", { name: "Add" }));
  const sheet = within(await screen.findByRole("dialog", { name: "Add a code project" }));
  expect(sheet.queryByRole("button", { name: /payments-repo/ })).toBeNull();
  await userEvent.type(sheet.getByRole("searchbox", { name: "Search code projects" }), "shared");
  await userEvent.click(sheet.getByRole("button", { name: /shared-lib/ }));
  await userEvent.click(sheet.getByRole("button", { name: "Add" }));
  await waitFor(() =>
    expect(writes(calls)).toEqual([
      [
        "POST /api/relations",
        { from_service_id: "v1", kind: "built_from", to_service_id: null, to_code_project_id: "r2", description: "" },
      ],
    ]),
  );
  await userEvent.click(list.getByRole("button", { name: "Remove payments-repo" }));
  await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Remove" }));
  await waitFor(() => expect(writes(calls).at(-1)).toEqual(["DELETE /api/relations/rel3?revision=5", null]));
});

test("Connections lists connections out and in, each linking to the other service, with Add connection", async () => {
  api();
  show("/services/v1/connections");
  const list = within(await screen.findByRole("region", { name: "Connections" }));
  expect(list.getByRole("link", { name: "→ postgres on db-1 (prod) :5432 tcp" })).toHaveAttribute(
    "href",
    "/services/v2/view",
  );
  expect(list.getByRole("link", { name: "← nginx on app-1 (prod) :8000 http" })).toHaveAttribute(
    "href",
    "/services/v3/view",
  );
  await userEvent.click(screen.getByRole("button", { name: "Add connection" }));
  expect(await screen.findByRole("dialog")).toBeInTheDocument();
});

test("Documents lists the service's documents", async () => {
  api();
  show("/services/v1/documents");
  const documents = within(await screen.findByRole("region", { name: "Documents" }));
  expect(await documents.findByRole("link", { name: "New document" })).toHaveAttribute(
    "href",
    "/documents/new?resource=service&resource_id=v1",
  );
});

test("Edit: the service's fields in place; Save sends the revision, Discard drops the changes", async () => {
  const calls = api({
    "PUT /api/services/v1": (body) => [200, { ...services[0], ...body, revision: 2 }],
  });
  show("/services/v1/edit");
  const name = await screen.findByLabelText("Name");
  expect(name).toHaveValue("payments-api");
  expect(screen.getByLabelText("Owner")).toHaveValue("payments team");
  expect(screen.queryByLabelText("Address")).toBeNull(); // only for an external service
  expect(screen.getByRole("button", { name: "Discard" })).toBeDisabled();
  await userEvent.clear(name);
  await userEvent.type(name, "payments");
  await userEvent.click(screen.getByRole("button", { name: "Discard" }));
  expect(screen.getByLabelText("Name")).toHaveValue("payments-api");
  await userEvent.type(screen.getByLabelText("Description"), " fast");
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(writes(calls)).toEqual([
      [
        "PUT /api/services/v1",
        {
          name: "payments-api",
          service_type: "custom",
          service_role: "application",
          owner: "payments team",
          description: "Takes payments fast",
          address: "",
          revision: 1,
        },
      ],
    ]),
  );
});

test("Edit: leaving with unsaved changes asks Leave or Stay", async () => {
  api();
  const router = show("/services/v1/edit");
  await userEvent.type(await screen.findByLabelText("Owner"), " B");
  await userEvent.click(nav().getByRole("link", { name: "Code (1)" }));
  const dialog = within(await screen.findByRole("alertdialog"));
  expect(dialog.getByText("Unsaved changes")).toBeInTheDocument();
  await userEvent.click(dialog.getByRole("button", { name: "Stay" }));
  expect(router.state.location.pathname).toBe("/services/v1/edit");
  expect(screen.getByLabelText("Owner")).toHaveValue("payments team B");
  await userEvent.click(nav().getByRole("link", { name: "Code (1)" }));
  await userEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Leave" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/services/v1/code"));
});

test("New service: the Edit page empty; Save creates the service and opens its View", async () => {
  const calls = api({
    "POST /api/services": (body) => [201, { ...body, id: "v1", revision: 1 }],
  });
  const router = show("/services/new");
  await userEvent.type(await screen.findByLabelText("Name"), "payments-api");
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/services/v1/view"));
  expect(writes(calls)).toEqual([
    [
      "POST /api/services",
      {
        name: "payments-api",
        service_type: "custom",
        service_role: "application",
        owner: "",
        description: "",
        address: "",
      },
    ],
  ]);
  expect(screen.queryByRole("alertdialog")).toBeNull();
});

test("View: Add deployment picks an environment and a server (members first), creates the deployment and reloads", async () => {
  const calls = api({
    "POST /api/deployments": (body) => [201, { ...body, id: "d9", status: "confirmed", revision: 1 }],
  });
  show("/services/v1/view");
  await userEvent.click(await screen.findByRole("button", { name: "Add deployment" }));
  const sheet = within(await screen.findByRole("dialog", { name: "Add deployment" }));
  expect(sheet.getByRole("button", { name: "Save" })).toBeDisabled();
  await userEvent.click(sheet.getByRole("combobox", { name: "Environment" }));
  await userEvent.click(await screen.findByRole("option", { name: "prod" }));
  await userEvent.click(sheet.getByRole("combobox", { name: "Server" }));
  const members = within(await screen.findByRole("group", { name: "In prod" }));
  expect(members.getAllByRole("option").map((item) => item.textContent)).toEqual(["app-1", "db-1"]);
  const others = within(screen.getByRole("group", { name: "Other servers" }));
  expect(others.getAllByRole("option").map((item) => item.textContent)).toEqual(["app-2", "new-1"]);
  await userEvent.click(others.getByRole("option", { name: "new-1" }));
  expect(sheet.getByText("Saving adds new-1 to prod.")).toBeInTheDocument();
  await userEvent.type(sheet.getByLabelText("Runtime"), "docker");
  await userEvent.type(sheet.getByLabelText("Runtime name"), "payments");
  await userEvent.type(sheet.getByLabelText("Port"), "9000");
  await userEvent.type(sheet.getByLabelText("Version"), "2.0");
  await userEvent.type(sheet.getByLabelText("Log location"), "stdout");
  const loads = () => calls.filter((call) => call.key === "GET /api/services/v1/deployments").length;
  expect(loads()).toBe(1);
  await userEvent.click(sheet.getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(writes(calls)).toEqual([
      [
        "POST /api/deployments",
        {
          service_id: "v1",
          environment_id: "e-prod",
          server_id: "s-new",
          runtime: "docker",
          runtime_name: "payments",
          port: 9000,
          deployed_version: "2.0",
          log_location: "stdout",
        },
      ],
    ]),
  );
  await waitFor(() => expect(loads()).toBe(2));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "Add deployment" })).toBeNull());
});

test("View: a refused Add deployment keeps the sheet open with the API's message", async () => {
  api({ "POST /api/deployments": () => [409, { error: { message: "This service already runs there" } }] });
  show("/services/v1/view");
  await userEvent.click(await screen.findByRole("button", { name: "Add deployment" }));
  const sheet = within(await screen.findByRole("dialog", { name: "Add deployment" }));
  await userEvent.click(sheet.getByRole("combobox", { name: "Environment" }));
  await userEvent.click(await screen.findByRole("option", { name: "prod" }));
  await userEvent.click(sheet.getByRole("combobox", { name: "Server" }));
  await userEvent.click(await screen.findByRole("option", { name: "app-1" }));
  expect(sheet.queryByText(/Saving adds/)).toBeNull();
  await userEvent.click(sheet.getByRole("button", { name: "Save" }));
  expect(await sheet.findByText("This service already runs there")).toBeInTheDocument();
});

test("Chat on a sub-page opens the chat panel with Conversations; a new chat starts from the service", async () => {
  const calls = api({
    "POST /api/sessions": () => [201, { id: "r9", title: "Chat r9", status: "starting", tags: ["services/v1"] }],
  });
  show("/services/v1/code");
  await userEvent.click(await screen.findByRole("button", { name: "Chat" }));
  const panel = within(await screen.findByTestId("resource-chat"));
  expect(panel.getByRole("button", { name: "Conversations" })).toBeInTheDocument();
  const pane = within(await screen.findByRole("region", { name: "Ask agent" }));
  await waitFor(() => expect(pane.getByRole("button", { name: "Settings" })).toBeEnabled());
  await userEvent.type(pane.getByLabelText("Message"), "What does it need?");
  await userEvent.click(pane.getByRole("button", { name: "Send message" }));
  await waitFor(() => expect(calls.some((call) => call.key === "POST /api/sessions")).toBe(true));
  expect(calls.find((call) => call.key === "POST /api/sessions")!.body.started_from).toEqual({ service_id: "v1" });
  expect(screen.getByRole("region", { name: "Built from" })).toBeInTheDocument();
});

test("Edit: type External shows Address and sends it; another type clears it", async () => {
  const calls = api({ "PUT /api/services/v1": (body) => [200, { ...services[0], ...body, revision: 2 }] });
  show("/services/v1/edit");
  await userEvent.click(await screen.findByRole("combobox", { name: "Type" }));
  await userEvent.click(await screen.findByRole("option", { name: "External service" }));
  await userEvent.type(screen.getByLabelText("Address"), " pay.example.com ");
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(writes(calls)).toHaveLength(1));
  expect(writes(calls)[0][1]).toMatchObject({ service_type: "external", address: "pay.example.com" });
});

test("Edit: a stale save says the service changed meanwhile and reloads", async () => {
  const calls = api({
    "PUT /api/services/v1": () => [409, { error: { message: "stale", code: "STALE_REVISION" } }],
  });
  show("/services/v1/edit");
  await userEvent.type(await screen.findByLabelText("Owner"), " B");
  const loads = () => calls.filter((call) => call.key === "GET /api/services").length;
  expect(loads()).toBe(1);
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText("This was changed meanwhile. Reload to see the latest.")).toBeInTheDocument();
  await waitFor(() => expect(loads()).toBe(2));
});

test("a notice does not follow to another tab", async () => {
  const list = [...services];
  api(
    {
      "PUT /api/services/v1": (body) => {
        list[0] = { ...list[0], ...body, revision: 2 };
        return [200, list[0]];
      },
    },
    list,
  );
  show("/services/v1/edit");
  await userEvent.type(await screen.findByLabelText("Owner"), " B");
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText("Service saved.")).toBeInTheDocument();
  await userEvent.click(nav().getByRole("link", { name: "Code (1)" }));
  await screen.findByRole("region", { name: "Built from" });
  expect(screen.queryByText("Service saved.")).toBeNull();
});
