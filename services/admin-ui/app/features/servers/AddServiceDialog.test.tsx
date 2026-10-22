import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router";
import type { EnvironmentModel } from "./environments";
import type { ServiceAsset } from "./services";
import AddServiceDialog from "./AddServiceDialog";
import EnvironmentWorkspace from "./EnvironmentWorkspace";

const asset = (id: string, name: string): ServiceAsset => ({
  id,
  project_id: "p",
  name,
  description: "",
  owner: "",
  address: null,
  revision: 1,
  service_type: "custom",
  service_role: "application",
  created_at: "2026-10-22T00:00:00Z",
  updated_at: "2026-10-22T00:00:00Z",
  repositories: [],
  deployments: [],
  dependencies: [],
  dependents: [],
  environments: [],
});
const services = [asset("v2", "worker"), asset("v1", "payments-api")];
const saved = (body: Record<string, unknown>) => ({ id: "d9", revision: 1, ...body });

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
afterEach(() => vi.unstubAllGlobals());

function dialog(onAdded = vi.fn()) {
  render(
    <AddServiceDialog environmentId="e1" server={{ id: "s1", name: "app-1" }} onAdded={onAdded} onClose={vi.fn()} />,
  );
  return onAdded;
}
async function deploymentFields(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("Runtime"), "docker");
  await user.type(screen.getByLabelText("Runtime name"), "payments");
  await user.type(screen.getByLabelText("Port"), "8000");
  await user.type(screen.getByLabelText("Version"), "1.4");
  await user.type(screen.getByLabelText("Log location"), "/var/log/payments");
}
const deployment = {
  server_id: "s1",
  environment_id: "e1",
  runtime: "docker",
  runtime_name: "payments",
  port: 8000,
  deployed_version: "1.4",
  log_location: "/var/log/payments",
};

test("the picker offers the recorded services by name, and a new one", async () => {
  const user = userEvent.setup();
  api({ "GET /api/services": () => [200, { services }] });
  dialog();
  expect(await screen.findByText("Deployment on app-1")).toBeInTheDocument();
  await user.click(await screen.findByRole("combobox", { name: "Service" }));
  expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual([
    "payments-api",
    "worker",
    "A new service",
  ]);
});

test("adding an existing service creates its deployment on the server here", async () => {
  const user = userEvent.setup();
  const calls = api({
    "GET /api/services": () => [200, { services }],
    "POST /api/deployments": (body) => [201, saved(body)],
  });
  const onAdded = dialog();
  await user.click(await screen.findByRole("combobox", { name: "Service" }));
  await user.click(screen.getByRole("option", { name: "payments-api" }));
  expect(screen.queryByLabelText("Name")).not.toBeInTheDocument();
  await deploymentFields(user);
  await user.click(screen.getByRole("button", { name: "Add service" }));
  await vi.waitFor(() => expect(onAdded).toHaveBeenCalledWith("v1"));
  expect(calls.map((call) => call.key)).toEqual(["GET /api/services", "POST /api/deployments"]);
  expect(calls[1].body).toEqual({ service_id: "v1", ...deployment });
});

test("adding a new service creates it, then its deployment", async () => {
  const user = userEvent.setup();
  const calls = api({
    "GET /api/services": () => [200, { services }],
    "POST /api/services": (body) => [201, { ...asset("v9", body.name), ...body }],
    "POST /api/deployments": (body) => [201, saved(body)],
  });
  const onAdded = dialog();
  await user.click(await screen.findByRole("combobox", { name: "Service" }));
  await user.click(screen.getByRole("option", { name: "A new service" }));
  await user.type(screen.getByLabelText("Name"), "ledger");
  await user.click(screen.getByRole("combobox", { name: "Type" }));
  await user.click(screen.getByRole("option", { name: "Custom application" }));
  await user.click(screen.getByRole("combobox", { name: "Role" }));
  await user.click(screen.getByRole("option", { name: "Worker" }));
  await user.type(screen.getByLabelText("Owner"), "payments team");
  await user.type(screen.getByLabelText("Description"), "Books entries");
  await deploymentFields(user);
  await user.click(screen.getByRole("button", { name: "Add service" }));
  await vi.waitFor(() => expect(onAdded).toHaveBeenCalledWith("v9"));
  expect(calls.find((call) => call.key === "POST /api/services")!.body).toEqual({
    name: "ledger",
    service_type: "custom",
    service_role: "worker",
    owner: "payments team",
    description: "Books entries",
  });
  expect(calls.at(-1)!.body).toEqual({ service_id: "v9", ...deployment });
});

test("a new external service has an Address field, sent with the service", async () => {
  const user = userEvent.setup();
  const calls = api({
    "GET /api/services": () => [200, { services }],
    "POST /api/services": (body) => [201, { ...asset("v9", body.name), ...body }],
    "POST /api/deployments": (body) => [201, saved(body)],
  });
  const onAdded = dialog();
  await user.click(await screen.findByRole("combobox", { name: "Service" }));
  await user.click(screen.getByRole("option", { name: "A new service" }));
  await user.type(screen.getByLabelText("Name"), "smtp relay");
  expect(screen.queryByLabelText("Address")).not.toBeInTheDocument();
  await user.click(screen.getByRole("combobox", { name: "Type" }));
  await user.click(screen.getByRole("option", { name: "External service" }));
  await user.type(screen.getByLabelText("Address"), " smtp.example.com ");
  await user.click(screen.getByRole("button", { name: "Add service" }));
  await vi.waitFor(() => expect(onAdded).toHaveBeenCalledWith("v9"));
  expect(calls.find((call) => call.key === "POST /api/services")!.body).toMatchObject({
    name: "smtp relay",
    service_type: "external",
    address: "smtp.example.com",
  });
});

test("a refused deployment keeps the dialog; saving again does not create the new service twice", async () => {
  const user = userEvent.setup();
  let refuse = true;
  const calls = api({
    "GET /api/services": () => [200, { services }],
    "POST /api/services": (body) => [201, { ...asset("v9", body.name), ...body }],
    "POST /api/deployments": (body) =>
      refuse ? [409, { error: { message: "This deployment already exists" } }] : [201, saved(body)],
  });
  const onAdded = dialog();
  await user.click(await screen.findByRole("combobox", { name: "Service" }));
  await user.click(screen.getByRole("option", { name: "A new service" }));
  await user.type(screen.getByLabelText("Name"), "ledger");
  await user.click(screen.getByRole("button", { name: "Add service" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("This deployment already exists");
  refuse = false;
  await user.click(screen.getByRole("button", { name: "Add service" }));
  await vi.waitFor(() => expect(onAdded).toHaveBeenCalledWith("v9"));
  expect(calls.filter((call) => call.key === "POST /api/services")).toHaveLength(1);
});

function Where() {
  return <output data-testid="where">{useLocation().search}</output>;
}

test("the workspace's Add service reloads the model and selects the service", async () => {
  const user = userEvent.setup();
  const base: EnvironmentModel = {
    environment: { id: "e1", name: "prod", description: "", revision: 1 },
    servers: [{ id: "s1", name: "app-1", hostname: "", description: "", tags: [], revision: 1, deployments: [] }],
    services: [],
    connections: [],
    relations: [],
    external: [],
    external_services: [],
  };
  let current = base;
  const calls = api({
    "GET /api/environments/e1/model": () => [200, current],
    "GET /api/services": () => [200, { services }],
    "POST /api/deployments": (body) => {
      current = {
        ...base,
        servers: [
          {
            ...base.servers[0],
            deployments: [
              {
                id: "d9",
                service_id: "v1",
                service_name: "payments-api",
                runtime: "",
                runtime_name: "",
                port: null,
                version: "",
                log_location: "",
                revision: 1,
              },
            ],
          },
        ],
      };
      return [201, saved(body)];
    },
    // The service column.
    "GET /api/services/v1/deployments": () => [200, { deployments: [] }],
    "GET /api/services/v1/connections": () => [200, { connections: [] }],
    "GET /api/services/v1/relations": () => [200, { relations: [] }],
    "GET /api/inventory/service/v1/documents": () => [200, { inventory_document: null, documents: [] }],
  });
  render(
    <MemoryRouter initialEntries={["/environments/e1?server=s1"]}>
      <EnvironmentWorkspace environmentId="e1" />
      <Where />
    </MemoryRouter>,
  );
  await user.click(await screen.findByRole("button", { name: "Add service" }));
  await user.click(await screen.findByRole("combobox", { name: "Service" }));
  await user.click(screen.getByRole("option", { name: "payments-api" }));
  await user.click(screen.getByRole("button", { name: "Add service" }));
  expect(await screen.findByRole("region", { name: "payments-api" })).toBeInTheDocument();
  expect(screen.getByTestId("where")).toHaveTextContent("?server=s1&service=v1");
  expect(calls.filter((call) => call.key === "GET /api/environments/e1/model")).toHaveLength(2);
});
