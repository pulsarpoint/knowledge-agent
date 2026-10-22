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

function dialog(props: { from?: string[]; connection?: Connection } = {}) {
  const handlers = { onSaved: vi.fn(), onConflict: vi.fn(), onClose: vi.fn() };
  render(
    <AddConnectionDialog from={props.from ?? ["d1"]} services={services} connection={props.connection} {...handlers} />,
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
    ["External services", ["stripe · api.stripe.com"]],
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
