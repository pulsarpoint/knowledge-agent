import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { serviceAsset as asset, serviceDeployment, stubApi as api } from "~/lib/__tests__/serviceFixture";
import type { Connection, Deployment, Relation } from "./inventory";
import ServicePanel from "./ServicePanel";

vi.mock("~/components/Markdown", () => ({ default: ({ content }: { content: string }) => <div>{content}</div> }));

const here = {
  ...serviceDeployment("d1", "prod", "app-1"),
  runtime_name: "payments",
  port: 8000,
  deployed_version: "1.4",
};
const services = [
  asset("v1", "payments-api", [here, serviceDeployment("d3", "staging", "app-2")], {
    description: "Takes payments",
    owner: "payments team",
    repositories: [
      {
        id: "r1",
        name: "payments-repo",
        remote_url: null,
        requested_ref: null,
        current_commit: null,
        checkout_path: null,
        synced_at: null,
        archived: false,
      },
    ],
  }),
  asset("v2", "postgres", [serviceDeployment("d2", "prod", "db-1")]),
  asset("v3", "nginx", [serviceDeployment("d4", "prod", "app-1")]),
  asset("v4", "stripe", [], { service_type: "external", address: "api.stripe.com" }),
  asset("v5", "billing"),
];
const deployments: Deployment[] = [
  {
    id: "d1",
    service_id: "v1",
    server_id: "s-app-1",
    environment_id: "e-prod",
    runtime: "docker",
    runtime_name: "payments",
    port: 8000,
    log_location: "",
    deployed_version: "1.4",
    status: "confirmed",
    revision: 2,
  },
  {
    id: "d3",
    service_id: "v1",
    server_id: "s-app-2",
    environment_id: "e-staging",
    runtime: "docker",
    runtime_name: "",
    port: null,
    log_location: "",
    deployed_version: "",
    status: "confirmed",
    revision: 1,
  },
];
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
  connection("c3", "d1", { to_service_id: "v4" }, 443, "https"),
  connection("c4", "d1", { to_host: "smtp.example.com" }, 25, "tcp"),
  connection("c5", "d3", { to_deployment_id: "d2" }, 5432, "tcp"), // from staging: not this deployment's
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
  relation("rel2", "v3", "depends_on", { to_service_id: "v1" }),
  relation("rel3", "v1", "built_from", { to_code_project_id: "r1" }),
  relation("rel4", "v1", "depends_on", { to_service_id: "v2" }),
  relation("rel5", "v5", "part_of", { to_service_id: "v1" }),
];
function routes(extra: Record<string, (body: any) => [number, unknown]> = {}) {
  return {
    "GET /api/services": () => [200, { services }] as [number, unknown],
    "GET /api/services/v1/deployments": () => [200, { deployments }] as [number, unknown],
    "GET /api/services/v1/connections": () => [200, { connections }] as [number, unknown],
    "GET /api/services/v1/relations": () => [200, { relations }] as [number, unknown],
    "GET /api/inventory/service/v1/documents": () =>
      [200, { inventory_document: null, documents: [] }] as [number, unknown],
    ...extra,
  };
}
afterEach(() => vi.unstubAllGlobals());

function panel(workspace = true) {
  const onChanged = vi.fn();
  render(
    <MemoryRouter>
      {workspace ? (
        <ServicePanel serviceId="v1" environmentId="e-prod" serverId="s-app-1" onChanged={onChanged} />
      ) : (
        <ServicePanel serviceId="v1" onChanged={onChanged} />
      )}
    </MemoryRouter>,
  );
  return onChanged;
}
const loads = (calls: { key: string }[]) =>
  calls.filter((call) => call.key === "GET /api/services/v1/connections").length;
const CONFLICT = "This was changed meanwhile. Reload to see the latest.";

test("the column shows the fields, the deployment here, where else it runs, connections both ways, relations, code projects and documents", async () => {
  api(routes());
  panel();
  const column = within(await screen.findByRole("region", { name: "payments-api" }));
  expect(column.getByText("Takes payments")).toBeInTheDocument();
  expect(column.getByText(/Custom application · Application · payments team/)).toBeInTheDocument();
  expect(
    within(column.getByRole("region", { name: "Deployment here" })).getByText("docker · payments · :8000 · 1.4"),
  ).toBeInTheDocument();
  expect(
    within(column.getByRole("region", { name: "Also runs on" })).getByRole("link", { name: "staging / app-2" }),
  ).toHaveAttribute("href", "/environments/e-staging?server=s-app-2&service=v1");
  const lines = within(column.getByRole("region", { name: "Connections" }))
    .getAllByRole("listitem")
    .map((item) => item.textContent);
  expect(lines).toEqual([
    "→ postgres on db-1 (prod) :5432 tcp",
    "→ stripe :443 https",
    "→ smtp.example.com :25 tcp",
    "← nginx on app-1 (prod) :8000 http",
  ]);
  const connectionsPart = within(column.getByRole("region", { name: "Connections" }));
  expect(connectionsPart.getByRole("link", { name: "→ stripe :443 https" })).toHaveAttribute("href", "/services/v4");
  expect(connectionsPart.getByRole("link", { name: "→ postgres on db-1 (prod) :5432 tcp" })).toHaveAttribute(
    "href",
    "/services/v2",
  );
  expect(connectionsPart.getByRole("link", { name: "← nginx on app-1 (prod) :8000 http" })).toHaveAttribute(
    "href",
    "/services/v3",
  );
  expect(connectionsPart.queryByRole("link", { name: "→ smtp.example.com :25 tcp" })).not.toBeInTheDocument();
  expect(
    within(column.getByRole("region", { name: "Relations" }))
      .getAllByRole("listitem")
      .map((item) => item.textContent),
  ).toEqual(["Part of billing", "Depends on postgres", "Needed by nginx", "Has part billing"]);
  expect(within(column.getByRole("region", { name: "Code projects" })).getByText("payments-repo")).toBeInTheDocument();
  expect(column.queryByRole("region", { name: "Documents" })).not.toBeInTheDocument();
  await userEvent.click(column.getByRole("tab", { name: "Documents" }));
  expect(await column.findByRole("region", { name: "Documents" })).toBeInTheDocument();
  expect(column.queryByRole("region", { name: "Code projects" })).not.toBeInTheDocument();
});

test("on the service page every deployment is listed with its environment and server, and all its connections", async () => {
  api(routes());
  panel(false);
  const runs = within(await screen.findByRole("region", { name: "Runs on" }));
  expect(runs.getAllByRole("listitem").map((item) => item.textContent)).toEqual([
    "prod / app-1 · docker · payments · :8000 · 1.4",
    "staging / app-2 · docker",
  ]);
  expect(within(screen.getByRole("region", { name: "Connections" })).getAllByRole("listitem")).toHaveLength(5);
  expect(screen.queryByRole("heading", { level: 2, name: "payments-api" })).not.toBeInTheDocument();
});

test("remove buttons send the revision they read, then reload", async () => {
  const user = userEvent.setup();
  const calls = api(
    routes({
      "DELETE /api/connections/c1?revision=3": () => [200, { id: "c1" }],
      "DELETE /api/relations/rel1?revision=5": () => [200, { id: "rel1" }],
      "DELETE /api/relations/rel3?revision=5": () => [200, { id: "rel3" }],
      "DELETE /api/deployments/d1?revision=2": () => [200, { id: "d1" }],
    }),
  );
  const onChanged = panel();
  await user.click(await screen.findByRole("button", { name: "Remove → postgres on db-1 (prod) :5432 tcp" }));
  await vi.waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
  await user.click(await screen.findByRole("button", { name: "Remove Part of billing" }));
  await vi.waitFor(() => expect(onChanged).toHaveBeenCalledTimes(2));
  await user.click(await screen.findByRole("button", { name: "Remove payments-repo" }));
  await vi.waitFor(() => expect(onChanged).toHaveBeenCalledTimes(3));
  await user.click(await screen.findByRole("button", { name: "Remove the deployment here" }));
  await vi.waitFor(() => expect(onChanged).toHaveBeenCalledTimes(4));
  expect(calls.filter((call) => call.key.startsWith("DELETE")).map((call) => call.key)).toEqual([
    "DELETE /api/connections/c1?revision=3",
    "DELETE /api/relations/rel1?revision=5",
    "DELETE /api/relations/rel3?revision=5",
    "DELETE /api/deployments/d1?revision=2",
  ]);
  await vi.waitFor(() => expect(loads(calls)).toBe(5));
});

test("a stale revision on remove shows the conflict text and reloads", async () => {
  const user = userEvent.setup();
  const calls = api(
    routes({
      "DELETE /api/relations/rel2?revision=5": () => [
        409,
        { error: { message: "This relation changed since you read it", code: "STALE_REVISION" } },
      ],
    }),
  );
  const onChanged = panel();
  await user.click(await screen.findByRole("button", { name: "Remove Needed by nginx" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(CONFLICT);
  await vi.waitFor(() => expect(loads(calls)).toBe(2));
  expect(onChanged).toHaveBeenCalled();
});

test("two people edit the same connection: the second save gets the conflict text and the column reloads", async () => {
  const user = userEvent.setup();
  const calls = api(
    routes({
      "PUT /api/connections/c2": () => [
        409,
        { error: { message: "This connection changed since you read it", code: "STALE_REVISION" } },
      ],
    }),
  );
  panel();
  await user.click(await screen.findByRole("button", { name: "Edit ← nginx on app-1 (prod) :8000 http" }));
  await user.clear(screen.getByLabelText("Port"));
  await user.type(screen.getByLabelText("Port"), "8080");
  await user.click(screen.getByRole("button", { name: "Save connection" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(CONFLICT);
  expect(calls.find((call) => call.key === "PUT /api/connections/c2")!.body).toMatchObject({ port: 8080, revision: 3 });
  expect(screen.queryByRole("button", { name: "Save connection" })).not.toBeInTheDocument();
  await vi.waitFor(() => expect(loads(calls)).toBe(2));
});

test("another refusal on save, such as a duplicate, shows the API's own message and keeps the sheet open", async () => {
  const user = userEvent.setup();
  api(routes({ "PUT /api/connections/c2": () => [409, { error: { message: "This connection already exists" } }] }));
  panel();
  await user.click(await screen.findByRole("button", { name: "Edit ← nginx on app-1 (prod) :8000 http" }));
  await user.click(screen.getByRole("button", { name: "Save connection" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("This connection already exists");
  expect(screen.queryByText(CONFLICT)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save connection" })).toBeInTheDocument();
});

test("another 409 on remove shows the API's own message", async () => {
  const user = userEvent.setup();
  api(routes({ "DELETE /api/relations/rel2?revision=5": () => [409, { error: { message: "Refused for a reason" } }] }));
  panel();
  await user.click(await screen.findByRole("button", { name: "Remove Needed by nginx" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Refused for a reason");
});

test("a duplicate on the fields sheet shows the API's own message", async () => {
  const user = userEvent.setup();
  api(
    routes({ "PUT /api/services/v1": () => [409, { error: { message: "A service with this name already exists" } }] }),
  );
  panel();
  await user.click(await screen.findByRole("button", { name: "Edit payments-api" }));
  await user.click(screen.getByRole("button", { name: "Save service" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("A service with this name already exists");
  expect(screen.getByRole("button", { name: "Save service" })).toBeInTheDocument();
});

test("Add connection starts from the deployment here and reloads after saving", async () => {
  const user = userEvent.setup();
  const calls = api(routes({ "POST /api/connections": (body) => [201, { id: "c9", revision: 1, ...body }] }));
  const onChanged = panel();
  await user.click(await screen.findByRole("button", { name: "Add connection" }));
  expect(screen.getByText("From payments-api on app-1 (prod).")).toBeInTheDocument();
  await user.click(screen.getByRole("combobox", { name: "Target" }));
  await user.click(screen.getByRole("option", { name: "nginx" }));
  await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Add connection" }));
  await vi.waitFor(() => expect(onChanged).toHaveBeenCalled());
  expect(calls.find((call) => call.key === "POST /api/connections")!.body).toMatchObject({
    from_deployment_id: "d1",
    to_deployment_id: "d4",
  });
  await vi.waitFor(() => expect(loads(calls)).toBe(2));
});

test("Add relation and add code project open the relation dialog with that kind", async () => {
  const user = userEvent.setup();
  api(routes({ "GET /api/repositories": () => [200, { repositories: [] }] }));
  panel();
  await user.click(await screen.findByRole("button", { name: "Add relation" }));
  expect(screen.getByRole("combobox", { name: "Kind" })).toHaveTextContent("Depends on");
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  await user.click(screen.getByRole("button", { name: "Add code project" }));
  expect(screen.getByRole("combobox", { name: "Kind" })).toHaveTextContent("Built from");
});

test("editing the fields sends the revision; the Address field appears only for type external", async () => {
  const user = userEvent.setup();
  const calls = api(routes({ "PUT /api/services/v1": (body) => [200, { ...services[0], ...body }] }));
  const onChanged = panel();
  await user.click(await screen.findByRole("button", { name: "Edit payments-api" }));
  expect(screen.queryByLabelText("Address")).not.toBeInTheDocument();
  await user.click(screen.getByRole("combobox", { name: "Type" }));
  await user.click(screen.getByRole("option", { name: "External service" }));
  await user.type(screen.getByLabelText("Address"), "pay.example.com");
  await user.click(screen.getByRole("button", { name: "Save service" }));
  await vi.waitFor(() => expect(onChanged).toHaveBeenCalled());
  expect(calls.find((call) => call.key === "PUT /api/services/v1")!.body).toEqual({
    name: "payments-api",
    service_type: "external",
    service_role: "application",
    owner: "payments team",
    description: "Takes payments",
    address: "pay.example.com",
    revision: 1,
  });
});

test("editing the deployment here sends its revision", async () => {
  const user = userEvent.setup();
  const calls = api(routes({ "PUT /api/deployments/d1": (body) => [200, { ...deployments[0], ...body }] }));
  const onChanged = panel();
  await user.click(await screen.findByRole("button", { name: "Edit the deployment here" }));
  await user.clear(screen.getByLabelText("Version"));
  await user.type(screen.getByLabelText("Version"), "1.5");
  await user.click(screen.getByRole("button", { name: "Save deployment" }));
  await vi.waitFor(() => expect(onChanged).toHaveBeenCalled());
  expect(calls.find((call) => call.key === "PUT /api/deployments/d1")!.body).toEqual({
    runtime: "docker",
    runtime_name: "payments",
    port: 8000,
    deployed_version: "1.5",
    log_location: "",
    revision: 2,
  });
});

test("a long description shows its first lines, with Show more and Show less", async () => {
  const long = "Runs nightly backups. ".repeat(30);
  api(
    routes({
      "GET /api/services": () => [200, { services: [{ ...services[0], description: long }] }],
    }),
  );
  panel();
  const column = within(await screen.findByRole("region", { name: "payments-api" }));
  expect(column.getByText(long.trim())).toHaveClass("line-clamp-4");
  await userEvent.click(column.getByRole("button", { name: "Show more" }));
  expect(column.getByText(long.trim())).not.toHaveClass("line-clamp-4");
  expect(column.getByRole("button", { name: "Show less" })).toBeInTheDocument();
});

test("drafts in the panel carry a Draft badge with Confirm and Discard: the service, deployments, connections, relations", async () => {
  const user = userEvent.setup();
  api(
    routes({
      "GET /api/services": () => [200, { services: [{ ...services[0], status: "draft" }, ...services.slice(1)] }],
      "GET /api/services/v1/deployments": () => [
        200,
        { deployments: [{ ...deployments[0], status: "draft" }, deployments[1]] },
      ],
      "GET /api/services/v1/connections": () => [
        200,
        { connections: [{ ...connections[0], status: "draft" }, ...connections.slice(1)] },
      ],
      "GET /api/services/v1/relations": () => [
        200,
        { relations: [{ ...relations[0], status: "draft" }, ...relations.slice(1)] },
      ],
      "POST /api/inventory/relation/rel1/discard?dry_run=1": () => [
        200,
        { discarded: [{ kind: "relation", id: "rel1", name: "payments-api part_of billing" }] },
      ],
    }),
  );
  panel();
  const column = within(await screen.findByRole("region", { name: "payments-api" }));
  expect(column.getAllByText("Draft")).toHaveLength(4);
  expect(column.getByRole("button", { name: "Confirm payments-api" })).toBeInTheDocument();
  expect(column.getByRole("button", { name: "Confirm the deployment here" })).toBeInTheDocument();
  const connectionsPart = within(column.getByRole("region", { name: "Connections" }));
  expect(connectionsPart.getAllByRole("button", { name: /^Confirm/ })).toHaveLength(1);
  const relationsPart = within(column.getByRole("region", { name: "Relations" }));
  await user.click(relationsPart.getByRole("button", { name: "Discard Part of billing" }));
  expect(await screen.findByRole("dialog", { name: "Discard Part of billing?" })).toBeInTheDocument();
});

test("a person's edit of a draft keeps it a draft; Confirm then confirms it with the new revision", async () => {
  const user = userEvent.setup();
  let current: Deployment = { ...deployments[0], status: "draft" };
  const calls = api(
    routes({
      "GET /api/services/v1/deployments": () => [200, { deployments: [current] }],
      "PUT /api/deployments/d1": (body) => {
        current = { ...current, deployed_version: body.deployed_version, revision: 3 }; // still a draft
        return [200, current];
      },
      "POST /api/inventory/deployment/d1/confirm?dry_run=1": () => [
        200,
        { confirmed: [{ kind: "deployment", id: "d1", name: "payments-api on app-1 in prod" }] },
      ],
      "POST /api/inventory/deployment/d1/confirm": () => {
        current = { ...current, status: "confirmed", revision: 4 };
        return [200, { confirmed: [{ kind: "deployment", id: "d1", name: "payments-api on app-1 in prod" }] }];
      },
    }),
  );
  const onChanged = panel();
  await user.click(await screen.findByRole("button", { name: "Edit the deployment here" }));
  await user.clear(screen.getByLabelText("Version"));
  await user.type(screen.getByLabelText("Version"), "1.5");
  await user.click(screen.getByRole("button", { name: "Save deployment" }));
  await vi.waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
  const here = within(await screen.findByRole("region", { name: "Deployment here" }));
  expect(await here.findByText(/1\.5/)).toBeInTheDocument();
  expect(here.getByText("Draft")).toBeInTheDocument();
  await user.click(here.getByRole("button", { name: "Confirm the deployment here" }));
  const sheet = within(await screen.findByRole("dialog", { name: "Confirm the deployment here?" }));
  expect(await sheet.findByText("Nothing else is confirmed with it.")).toBeInTheDocument();
  await user.click(sheet.getByRole("button", { name: "Confirm" }));
  await vi.waitFor(() => expect(onChanged).toHaveBeenCalledTimes(2));
  expect(calls.find((call) => call.key === "POST /api/inventory/deployment/d1/confirm")!.body).toEqual({ revision: 3 });
  await vi.waitFor(() => expect(here.queryByText("Draft")).not.toBeInTheDocument());
});

test("a new refresh number reloads the panel (the page chat's agent may have written drafts)", async () => {
  const calls = api(routes());
  const view = (refresh: number) => (
    <MemoryRouter>
      <ServicePanel serviceId="v1" refresh={refresh} />
    </MemoryRouter>
  );
  const { rerender } = render(view(0));
  await screen.findByRole("region", { name: "payments-api" });
  expect(loads(calls)).toBe(1);
  rerender(view(1));
  await vi.waitFor(() => expect(loads(calls)).toBe(2));
});
