import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Connection } from "./inventory";
import { serviceAsset as asset, serviceDeployment as deployment, stubApi as api } from "~/lib/__tests__/serviceFixture";
import AddConnectionDialog from "./AddConnectionDialog";

const services = [
  asset("v1", "payments-api", [deployment("d1", "prod", "app-1"), deployment("d3", "staging", "app-2")]),
  asset("v2", "postgres", [deployment("d2", "prod", "db-1")]),
  asset("v3", "nginx", [deployment("d4", "prod", "app-1")]),
  asset("v4", "stripe", [], { service_type: "external", address: "api.stripe.com" }),
];

afterEach(() => vi.unstubAllGlobals());

function dialog(props: { from?: string[]; connection?: Connection; services?: typeof services } = {}) {
  const handlers = { onSaved: vi.fn(), onConflict: vi.fn(), onClose: vi.fn() };
  render(
    <AddConnectionDialog
      from={props.from ?? ["d1"]}
      services={props.services ?? services}
      connection={props.connection}
      {...handlers}
    />,
  );
  return handlers;
}
const created = (body: Record<string, unknown>) => [201, { id: "c9", revision: 1, ...body }] as [number, unknown];

test("the target picker groups deployments by environment and server, then external services, then a host", async () => {
  const user = userEvent.setup();
  dialog();
  expect(screen.getByText("From payments-api on app-1 (prod).")).toBeInTheDocument();
  await user.click(screen.getByRole("combobox", { name: "Target" }));
  const listbox = screen.getByRole("listbox");
  const groups = within(listbox).getAllByRole("group");
  expect(
    groups.map((group) => [
      group.firstChild?.textContent,
      within(group)
        .getAllByRole("option")
        .map((o) => o.textContent),
    ]),
  ).toEqual([
    ["prod / app-1", ["nginx"]],
    ["prod / db-1", ["postgres"]],
    ["staging / app-2", ["payments-api"]],
    ["External services", ["stripe · api.stripe.com", "New external service"]],
    ["Other", ["A host"]],
  ]);
});

test("a connection to a deployment", async () => {
  const user = userEvent.setup();
  const calls = api({ "POST /api/connections": created });
  const { onSaved } = dialog();
  await user.click(screen.getByRole("combobox", { name: "Target" }));
  await user.click(screen.getByRole("option", { name: "postgres" }));
  await user.type(screen.getByLabelText("Port"), "5432");
  await user.type(screen.getByLabelText("Description"), "orders");
  await user.click(screen.getByRole("button", { name: "Add connection" }));
  await vi.waitFor(() => expect(onSaved).toHaveBeenCalled());
  expect(calls[0].body).toEqual({
    from_deployment_id: "d1",
    to_deployment_id: "d2",
    to_service_id: null,
    to_host: null,
    port: 5432,
    protocol: "tcp",
    description: "orders",
  });
});

test("a connection to an external service", async () => {
  const user = userEvent.setup();
  const calls = api({ "POST /api/connections": created });
  const { onSaved } = dialog();
  await user.click(screen.getByRole("combobox", { name: "Target" }));
  await user.click(screen.getByRole("option", { name: "stripe · api.stripe.com" }));
  await user.type(screen.getByLabelText("Port"), "443");
  await user.clear(screen.getByLabelText("Protocol"));
  await user.type(screen.getByLabelText("Protocol"), "https");
  await user.click(screen.getByRole("button", { name: "Add connection" }));
  await vi.waitFor(() => expect(onSaved).toHaveBeenCalled());
  expect(calls[0].body).toMatchObject({
    to_deployment_id: null,
    to_service_id: "v4",
    to_host: null,
    port: 443,
    protocol: "https",
  });
});

test("the External services group shows with no external services yet, offering a new one", async () => {
  const user = userEvent.setup();
  dialog({ services: services.filter((service) => service.service_type !== "external") });
  await user.click(screen.getByRole("combobox", { name: "Target" }));
  const group = within(screen.getByRole("listbox"))
    .getAllByRole("group")
    .find((item) => item.firstChild?.textContent === "External services")!;
  expect(
    within(group)
      .getAllByRole("option")
      .map((o) => o.textContent),
  ).toEqual(["New external service"]);
});

test("a new external service is created first, then the connection; a retry reuses the created service", async () => {
  const user = userEvent.setup();
  let refuse = true;
  const calls = api({
    "POST /api/services": (body) => [201, { id: "v9", revision: 1, ...body }],
    "POST /api/connections": (body) =>
      refuse ? [400, { error: { message: "Port must be between 1 and 65535" } }] : created(body),
  });
  const { onSaved } = dialog();
  await user.click(screen.getByRole("combobox", { name: "Target" }));
  await user.click(screen.getByRole("option", { name: "New external service" }));
  await user.type(screen.getByLabelText("Name"), "sendgrid");
  await user.type(screen.getByLabelText("Address"), "api.sendgrid.com");
  await user.type(screen.getByLabelText("Service description"), "Sends email");
  await user.type(screen.getByLabelText("Port"), "443");
  await user.click(screen.getByRole("button", { name: "Add connection" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Port must be between 1 and 65535");
  refuse = false;
  await user.click(screen.getByRole("button", { name: "Add connection" }));
  await vi.waitFor(() => expect(onSaved).toHaveBeenCalled());
  expect(calls.map((call) => call.key)).toEqual([
    "POST /api/services",
    "POST /api/connections",
    "POST /api/connections",
  ]);
  expect(calls[0].body).toEqual({
    name: "sendgrid",
    service_type: "external",
    address: "api.sendgrid.com",
    description: "Sends email",
  });
  expect(calls[2].body).toMatchObject({
    from_deployment_id: "d1",
    to_service_id: "v9",
    to_deployment_id: null,
    port: 443,
  });
});

test("a connection to a host; a refusal stays in the dialog", async () => {
  const user = userEvent.setup();
  let refuse = true;
  const calls = api({
    "POST /api/connections": (body) =>
      refuse ? [400, { error: { message: "A connection to an external host needs a port" } }] : created(body),
  });
  const { onSaved } = dialog();
  await user.click(screen.getByRole("combobox", { name: "Target" }));
  await user.click(screen.getByRole("option", { name: "A host" }));
  await user.type(screen.getByLabelText("Host"), "smtp.example.com");
  await user.click(screen.getByRole("button", { name: "Add connection" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("A connection to an external host needs a port");
  refuse = false;
  await user.type(screen.getByLabelText("Port"), "25");
  await user.click(screen.getByRole("button", { name: "Add connection" }));
  await vi.waitFor(() => expect(onSaved).toHaveBeenCalled());
  expect(calls[1].body).toMatchObject({
    to_deployment_id: null,
    to_service_id: null,
    to_host: "smtp.example.com",
    port: 25,
  });
});

test("several deployments to start from: choose one", async () => {
  const user = userEvent.setup();
  const calls = api({ "POST /api/connections": created });
  const { onSaved } = dialog({ from: ["d1", "d3"] });
  await user.click(screen.getByRole("combobox", { name: "From" }));
  await user.click(screen.getByRole("option", { name: "payments-api on app-2 (staging)" }));
  await user.click(screen.getByRole("combobox", { name: "Target" }));
  await user.click(screen.getByRole("option", { name: "postgres" }));
  await user.click(screen.getByRole("button", { name: "Add connection" }));
  await vi.waitFor(() => expect(onSaved).toHaveBeenCalled());
  expect(calls[0].body).toMatchObject({ from_deployment_id: "d3", to_deployment_id: "d2", port: null });
});

const saved: Connection = {
  id: "c1",
  from_deployment_id: "d1",
  to_deployment_id: "d2",
  to_service_id: null,
  to_host: null,
  port: 5432,
  protocol: "tcp",
  description: "",
  status: "confirmed",
  revision: 3,
};

test("editing a connection sends the revision it read", async () => {
  const user = userEvent.setup();
  const calls = api({ "PUT /api/connections/c1": (body) => [200, { ...saved, ...body, revision: 4 }] });
  const { onSaved } = dialog({ connection: saved });
  expect(screen.getByRole("combobox", { name: "Target" })).toHaveTextContent("postgres");
  await user.clear(screen.getByLabelText("Port"));
  await user.type(screen.getByLabelText("Port"), "6432");
  await user.click(screen.getByRole("button", { name: "Save connection" }));
  await vi.waitFor(() => expect(onSaved).toHaveBeenCalled());
  expect(calls[0].body).toEqual({
    to_deployment_id: "d2",
    to_service_id: null,
    to_host: null,
    port: 6432,
    protocol: "tcp",
    description: "",
    revision: 3,
  });
});

test("a stale revision on save is a conflict for the panel", async () => {
  const user = userEvent.setup();
  api({
    "PUT /api/connections/c1": () => [
      409,
      { error: { message: "This connection changed since you read it", code: "STALE_REVISION" } },
    ],
  });
  const { onSaved, onConflict } = dialog({ connection: saved });
  await user.click(screen.getByRole("button", { name: "Save connection" }));
  await vi.waitFor(() => expect(onConflict).toHaveBeenCalled());
  expect(onSaved).not.toHaveBeenCalled();
});

test("a duplicate on save shows the API's message, not a conflict", async () => {
  const user = userEvent.setup();
  api({ "PUT /api/connections/c1": () => [409, { error: { message: "This connection already exists" } }] });
  const { onConflict } = dialog({ connection: saved });
  await user.click(screen.getByRole("button", { name: "Save connection" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("This connection already exists");
  expect(onConflict).not.toHaveBeenCalled();
});

test("a connection to a draft deployment says saving confirms the drafts it uses", async () => {
  const user = userEvent.setup();
  const drafted = [
    services[0],
    asset("v2", "postgres", [{ ...deployment("d2", "prod", "db-1"), status: "draft" }], { status: "draft" }),
    ...services.slice(2),
  ];
  dialog({ services: drafted });
  expect(screen.queryByText(/Saving confirms/)).not.toBeInTheDocument();
  await user.click(screen.getByRole("combobox", { name: "Target" }));
  await user.click(screen.getByRole("option", { name: "postgres" }));
  expect(
    screen.getByText("Saving confirms the drafts it uses: postgres on db-1 (prod), postgres."),
  ).toBeInTheDocument();
});

test("editing a draft connection keeps it a draft: no notice", () => {
  const drafted = [
    services[0],
    asset("v2", "postgres", [{ ...deployment("d2", "prod", "db-1"), status: "draft" }]),
    ...services.slice(2),
  ];
  dialog({ services: drafted, connection: { ...saved, status: "draft" } });
  expect(screen.queryByText(/Saving confirms/)).not.toBeInTheDocument();
});
