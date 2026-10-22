import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router";
import type { EnvironmentModel } from "./environments";
import { serviceAsset, serviceDeployment } from "~/lib/__tests__/serviceFixture";
import EnvironmentWorkspace from "./EnvironmentWorkspace";

const deployment = (id: string, service_id: string, service_name: string, port: number | null) => ({
  id,
  service_id,
  service_name,
  runtime: "docker",
  runtime_name: service_name,
  port,
  version: "1.4",
  log_location: "",
  revision: 1,
});
const server = (id: string, name: string, deployments: ReturnType<typeof deployment>[]) => ({
  id,
  name,
  hostname: `${name}.internal`,
  description: "",
  tags: [],
  revision: 1,
  deployments,
});
function model(): EnvironmentModel {
  return {
    environment: { id: "e1", name: "prod", description: "", revision: 1 },
    servers: [
      server("s1", "app-1", [deployment("d1", "v1", "payments-api", 8000), deployment("d2", "v2", "nginx", 80)]),
      server("s3", "cache-1", []),
    ],
    services: [],
    connections: [],
    relations: [],
    external: [],
    external_services: [],
  };
}

/** The service column's reads for the services on app-1. */
function column(): Record<string, (body: any) => [number, unknown]> {
  const place = (id: string) => ({ ...serviceDeployment(id, "prod", "app-1"), server_id: "s1", environment_id: "e1" });
  const services = [serviceAsset("v1", "payments-api", [place("d1")]), serviceAsset("v2", "nginx", [place("d2")])];
  const routes: Record<string, (body: any) => [number, unknown]> = { "GET /api/services": () => [200, { services }] };
  for (const [service, item] of [
    ["v1", deployment("d1", "v1", "payments-api", 8000)],
    ["v2", deployment("d2", "v2", "nginx", 80)],
  ] as const) {
    const saved = { ...item, server_id: "s1", environment_id: "e1", deployed_version: item.version };
    routes[`GET /api/services/${service}/deployments`] = () => [200, { deployments: [saved] }];
    routes[`GET /api/services/${service}/connections`] = () => [200, { connections: [] }];
    routes[`GET /api/services/${service}/relations`] = () => [200, { relations: [] }];
    routes[`GET /api/inventory/service/${service}/documents`] = () => [
      200,
      { inventory_document: null, documents: [] },
    ];
  }
  return routes;
}

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
      return status === 204 ? new Response(null, { status }) : new Response(JSON.stringify(value), { status });
    }),
  );
  return calls;
}
afterEach(() => vi.unstubAllGlobals());

function Where() {
  const location = useLocation();
  return <output data-testid="where">{location.search}</output>;
}
function show(search = "") {
  render(
    <MemoryRouter initialEntries={[`/environments/e1${search}`]}>
      <EnvironmentWorkspace environmentId="e1" />
      <Where />
    </MemoryRouter>,
  );
}
const where = () => screen.getByTestId("where").textContent;

test("the servers column lists the members; an empty member says no services", async () => {
  api({ "GET /api/environments/e1/model": () => [200, model()] });
  show();
  const servers = within(await screen.findByRole("region", { name: "Servers" }));
  expect(servers.getByRole("button", { name: /^app-1/ })).toHaveTextContent("2 services");
  expect(servers.getByRole("button", { name: /^cache-1/ })).toHaveTextContent("no services");
  expect(screen.queryByRole("region", { name: /Services on/ })).not.toBeInTheDocument();
});

test("clicking a server selects it; clicking a service selects it; other params stay", async () => {
  const user = userEvent.setup();
  api({ "GET /api/environments/e1/model": () => [200, model()], ...column() });
  show("?chat=r1");
  await user.click(await screen.findByRole("button", { name: /^app-1/ }));
  expect(where()).toBe("?chat=r1&server=s1");
  const services = within(screen.getByRole("region", { name: "Services on app-1" }));
  expect(services.getAllByRole("button", { name: /^(payments-api|nginx)/ }).map((b) => b.textContent)).toEqual([
    expect.stringContaining("payments-api"),
    expect.stringContaining("nginx"),
  ]);
  await user.click(services.getByRole("button", { name: /^payments-api/ }));
  expect(where()).toBe("?chat=r1&server=s1&service=v1");
  const service = within(await screen.findByRole("region", { name: "payments-api" }));
  expect(service.getByText(/docker · payments-api · :8000 · 1\.4/)).toBeInTheDocument();
  // Another server drops the service.
  await user.click(screen.getByRole("button", { name: /^cache-1/ }));
  expect(where()).toBe("?chat=r1&server=s3");
  expect(screen.getByRole("region", { name: "Services on cache-1" })).toHaveTextContent(
    "No services on this server in this environment.",
  );
});

test("the selection comes from the URL; back links step to the previous column", async () => {
  const user = userEvent.setup();
  api({ "GET /api/environments/e1/model": () => [200, model()], ...column() });
  show("?server=s1&service=v2");
  expect(await screen.findByRole("region", { name: "nginx" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Back to services on app-1" }));
  expect(where()).toBe("?server=s1");
  await user.click(screen.getByRole("button", { name: "Back to servers" }));
  expect(where()).toBe("");
});

test("stale ids in the URL fall back to nothing selected, without an error", async () => {
  api({ "GET /api/environments/e1/model": () => [200, model()] });
  show("?server=gone&service=v1");
  expect(await screen.findByRole("region", { name: "Servers" })).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: /Services on/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "payments-api" })).not.toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("a stale service on a live server selects only the server", async () => {
  api({ "GET /api/environments/e1/model": () => [200, model()] });
  show("?server=s3&service=v1");
  expect(await screen.findByRole("region", { name: "Services on cache-1" })).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "payments-api" })).not.toBeInTheDocument();
});

test("a server whose last deployment was removed stays listed (empty)", async () => {
  const after = model();
  after.servers[0].deployments = [];
  api({ "GET /api/environments/e1/model": () => [200, after] });
  show("?server=s1");
  expect(await screen.findByRole("button", { name: /^app-1/ })).toHaveTextContent("no services");
  expect(screen.getByRole("region", { name: "Services on app-1" })).toBeInTheDocument();
});

test("Remove from environment shows the refusal while deployments exist", async () => {
  const user = userEvent.setup();
  const calls = api({
    "GET /api/environments/e1/model": () => [200, model()],
    "DELETE /api/environments/e1/servers/s1": () => [
      400,
      { error: { message: "Remove the deployments on this server in this environment first." } },
    ],
  });
  show("?server=s1");
  await user.click(await screen.findByRole("button", { name: "Remove from environment app-1" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Remove the deployments on this server in this environment first.",
  );
  expect(calls.filter((call) => call.key === "GET /api/environments/e1/model")).toHaveLength(1);
  expect(where()).toBe("?server=s1");
});

test("Remove from environment of an empty member reloads and clears the selection", async () => {
  const user = userEvent.setup();
  let current = model();
  const calls = api({
    "GET /api/environments/e1/model": () => [200, current],
    "DELETE /api/environments/e1/servers/s3": () => {
      current = { ...current, servers: current.servers.filter((item) => item.id !== "s3") };
      return [204, null];
    },
  });
  show("?server=s3");
  await user.click(await screen.findByRole("button", { name: "Remove from environment cache-1" }));
  expect(await screen.findByRole("button", { name: /^app-1/ })).toBeInTheDocument();
  await screen.findByText(
    (_, element) => element?.getAttribute("data-testid") === "where" && element.textContent === "",
  );
  expect(screen.queryByRole("button", { name: /^cache-1/ })).not.toBeInTheDocument();
  expect(calls.filter((call) => call.key === "GET /api/environments/e1/model")).toHaveLength(2);
});

test("column 3 is the service panel; a change there reloads the workspace", async () => {
  const user = userEvent.setup();
  const calls = api({
    "GET /api/environments/e1/model": () => [200, model()],
    ...column(),
    "DELETE /api/deployments/d1?revision=1": () => [200, { id: "d1" }],
  });
  show("?server=s1&service=v1");
  const service = within(await screen.findByRole("region", { name: "payments-api" }));
  expect(service.getByRole("region", { name: "Connections" })).toBeInTheDocument();
  expect(service.getByRole("link", { name: "Service page" })).toHaveAttribute("href", "/services/v1");
  await user.click(service.getByRole("button", { name: "Remove the deployment here" }));
  await vi.waitFor(() => expect(calls.filter((call) => call.key === "GET /api/environments/e1/model")).toHaveLength(2));
});
