import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router";
import EnvironmentsView from "~/features/servers/EnvironmentsView";
import type { EnvironmentAsset } from "~/features/servers/environments";
import type { ServiceAsset } from "~/features/servers/services";

const engine = vi.hoisted(() => ({ compile: vi.fn(), render: vi.fn(), dispose: vi.fn() }));
vi.mock("@d2lang/d2", () => ({
  D2: vi.fn(function () {
    return engine;
  }),
}));
beforeEach(() => {
  vi.clearAllMocks();
  engine.compile.mockResolvedValue({ diagram: {}, renderOptions: {} });
  engine.render.mockResolvedValue(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 400"><text>Graph</text></svg>',
  );
  engine.dispose.mockResolvedValue(undefined);
});

const base: ServiceAsset = {
  id: "api",
  project_id: "project",
  name: "API",
  description: "",
  owner: "",
  operations_notes: "",
  revision: 1,
  service_type: "custom",
  service_role: "application",
  created_at: "2026-09-13T00:00:00Z",
  updated_at: "2026-09-13T00:00:00Z",
  repositories: [],
  deployments: [],
  dependencies: [],
  dependents: [],
  environments: [],
};
const services: ServiceAsset[] = [
  {
    ...base,
    dependencies: [
      { service_id: "db", name: "Database", description: "Stores records" },
      { service_id: "outside", name: "Outside", description: "Outside this environment" },
    ],
  },
  { ...base, id: "db", name: "Database", service_type: "builtin", service_role: "database" },
  { ...base, id: "worker", name: "Worker", service_role: "worker" },
  {
    ...base,
    id: "outside",
    name: "Outside",
    service_role: "proxy",
    dependencies: [{ service_id: "api", name: "API", description: "Other environment" }],
  },
];
const development: EnvironmentAsset = {
  id: "dev",
  project_id: "project",
  name: "Development",
  description: "Daily testing",
  revision: 1,
  created_at: "2026-09-13T00:00:00Z",
  updated_at: "2026-09-13T00:00:00Z",
  services: services.slice(0, 3).map(({ id, name }) => ({ id, name })),
  servers: [],
};
const staging = { ...development, id: "staging", name: "Staging", services: [development.services[1]] };
function Location() {
  return <output data-testid="location">{useLocation().search}</output>;
}
function setup(url = "/environments", environments = [development, staging]) {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async (url: string) =>
        new Response(
          JSON.stringify(
            url.endsWith("/services") ? { services } : url.endsWith("/servers") ? { servers: [] } : { environments },
          ),
        ),
    ),
  );
  render(
    <MemoryRouter initialEntries={[url]}>
      <EnvironmentsView />
      <Location />
    </MemoryRouter>,
  );
}

test("row Graph action opens TALA for that environment and excludes outside nodes and links", async () => {
  setup();
  await userEvent.click(await screen.findByRole("button", { name: "View Development graph" }));
  expect(screen.getByRole("tab", { name: "Dependency graph" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("combobox", { name: "Environment" })).toHaveTextContent("Development");
  expect(screen.getByTestId("location")).toHaveTextContent("view=graph&environment_id=dev");
  expect(await screen.findByRole("img", { name: /Service dependencies laid out by TALA/ })).toBeInTheDocument();
  expect(screen.getByText("3 services · 1 relationship")).toBeInTheDocument();
  const source = engine.compile.mock.calls[0][0];
  expect(source).toContain("service0 -> service1");
  expect(source).toContain("Worker");
  expect(source).not.toContain("Outside");
  const relationships = within(screen.getByRole("region", { name: "Relationship details" }));
  expect(relationships.getByText("Stores records")).toBeInTheDocument();
  expect(relationships.queryByText(/Outside/)).toBeNull();
  expect(within(screen.getByRole("list", { name: "Service role colors" })).queryByText("Proxy")).toBeNull();
  await userEvent.click(relationships.getByRole("button", { name: "Database" }));
  expect(screen.getByRole("button", { name: "Close service details" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("tab", { name: "Interactive (Dagre)" }));
  expect(await screen.findByRole("button", { name: "Open Worker details" })).toBeInTheDocument();
  expect(screen.getByTestId("location")).toHaveTextContent("environment_id=dev&layout=dagre");
});

test("Dagre deep link retains colors, node interaction and resets when choosing another environment", async () => {
  setup("/environments?view=graph&environment_id=dev&layout=dagre");
  const node = await screen.findByRole("group", { name: "Service: API" });
  const original = node.style.transform;
  expect(node).toHaveStyle({ backgroundColor: "#dbeafe" });
  expect(screen.getByRole("group", { name: "Service: Database" })).toHaveStyle({ backgroundColor: "#ede9fe" });
  fireEvent.keyDown(node, { key: "Enter" });
  fireEvent.keyDown(node, { key: "ArrowRight" });
  await waitFor(() => expect(node.style.transform).not.toBe(original));
  fireEvent.keyDown(node, { key: "Delete" });
  expect(node).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Auto layout" }));
  await waitFor(() => expect(node.style.transform).toBe(original));
  await userEvent.click(screen.getByRole("button", { name: "Open Worker details" }));
  expect(screen.getByRole("button", { name: "Close service details" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("combobox", { name: "Environment" }));
  await userEvent.click(screen.getByRole("option", { name: "Staging" }));
  expect(screen.getByText("1 service · 0 relationships")).toBeInTheDocument();
  expect(screen.queryByRole("group", { name: "Service: API" })).toBeNull();
  expect(screen.queryByRole("group", { name: "Service: Worker" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Close service details" })).toBeNull();
  expect(screen.queryByRole("region", { name: "Relationship details" })).toBeNull();
  expect(screen.getByTestId("location")).toHaveTextContent("environment_id=staging&layout=dagre");
  await userEvent.click(screen.getByRole("tab", { name: "Environment list" }));
  expect(screen.getByRole("textbox", { name: "Filter environments" })).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Service dependency graph" })).toBeNull();
});

test.each(["", "&environment_id=missing"])(
  "graph requires an available selected environment (%s)",
  async (selection) => {
    setup(`/environments?view=graph${selection}`);
    expect(await screen.findByRole("combobox", { name: "Environment" })).toHaveTextContent("Choose an environment");
    if (selection) expect(screen.getByText("Environment not available")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Service dependency graph" })).toBeNull();
    expect(engine.compile).not.toHaveBeenCalled();
  },
);

test("an environment without services offers editing instead of a global graph", async () => {
  setup("/environments?view=graph&environment_id=empty", [{ ...development, id: "empty", services: [] }]);
  expect(await screen.findByText("No services in this environment")).toBeInTheDocument();
  expect(engine.compile).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "Select services" }));
  expect(screen.getByRole("textbox", { name: "Environment name" })).toHaveValue("Development");
  expect(screen.getByRole("checkbox", { name: "API" })).not.toBeChecked();
  expect(screen.queryByRole("combobox", { name: "Environment" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(await screen.findByText("No services in this environment")).toBeInTheDocument();
});

test("refreshing membership removes nodes, links and details from the current graph", async () => {
  const inventory = [development];
  setup("/environments?view=graph&environment_id=dev&layout=dagre", inventory);
  await userEvent.click(await screen.findByRole("button", { name: "Open API details" }));
  inventory.splice(0, 1, { ...development, services: [development.services[1]] });
  await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
  expect(await screen.findByText("1 service · 0 relationships")).toBeInTheDocument();
  expect(screen.queryByRole("group", { name: "Service: API" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Close service details" })).toBeNull();
  expect(screen.queryByRole("region", { name: "Relationship details" })).toBeNull();
  inventory.splice(0);
  await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
  expect(await screen.findByText("No environments yet")).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Service dependency graph" })).toBeNull();
});
