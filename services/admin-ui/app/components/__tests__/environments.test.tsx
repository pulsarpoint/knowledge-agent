import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import EnvironmentsView from "~/features/servers/EnvironmentsView";
import type { EnvironmentAsset, EnvironmentInput } from "~/features/servers/environments";
import type { ServiceAsset } from "~/features/servers/services";
import type { ServerAsset } from "~/features/servers/api";

const services: ServiceAsset[] = ["API", "Database"].map((name, index) => ({
  id: `service-${index}`,
  project_id: "project",
  name,
  description: "",
  owner: "",
  operations_notes: "",
  revision: 1,
  service_type: index ? "builtin" : "custom",
  service_role: index ? "database" : "application",
  created_at: "2026-09-13T00:00:00Z",
  updated_at: "2026-09-13T00:00:00Z",
  repositories: [],
  deployments: [],
  dependencies: [],
  dependents: [],
  environments: [],
}));
const servers: ServerAsset[] = ["App host", "Data host"].map((name, index) => ({
  id: `server-${index}`,
  name,
  hostname: `host${index}.internal`,
  description: "",
  tags: [],
  revision: 1,
  ssh_port: 22,
  ssh_username: null,
  access_enabled: false,
  allow_sudo: false,
  ssh_allowed: false,
  created_at: "2026-09-13T00:00:00Z",
  updated_at: "2026-09-13T00:00:00Z",
}));
const development: EnvironmentAsset = {
  id: "development",
  project_id: "project",
  name: "Development",
  description: "Daily testing",
  revision: 3,
  created_at: "2026-09-13T00:00:00Z",
  updated_at: "2026-09-13T00:00:00Z",
  services: services.map(({ id, name }) => ({ id, name })),
  servers: servers.map(({ id, name, hostname }) => ({ id, name, hostname })),
};

function setup(
  environments: EnvironmentAsset[] = [],
  options: {
    emptyInventory?: boolean;
    saveStatus?: number;
    loadError?: boolean;
    onDelete?: () => Promise<Response>;
  } = {},
) {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "DELETE") {
      if (options.onDelete) return options.onDelete();
      environments.splice(0);
      return new Response(JSON.stringify({ id: development.id }));
    }
    if (init?.method === "POST" || init?.method === "PUT") {
      if (options.saveStatus)
        return new Response(
          JSON.stringify({ error: { message: "This environment changed. Cancel and reopen the form before saving" } }),
          { status: options.saveStatus },
        );
      const value = JSON.parse(init.body as string) as EnvironmentInput;
      const saved = {
        ...development,
        name: value.name,
        description: value.description,
        revision: (value.revision ?? 0) + 1,
        services: development.services.filter((service) => value.service_ids.includes(service.id)),
        servers: development.servers.filter((server) => value.server_ids.includes(server.id)),
      };
      environments.splice(0, environments.length, saved);
      return new Response(JSON.stringify(saved), { status: init.method === "POST" ? 201 : 200 });
    }
    if (options.loadError)
      return new Response(JSON.stringify({ error: { message: "Inventory unavailable" } }), { status: 503 });
    return new Response(
      JSON.stringify(
        url.endsWith("/services")
          ? { services: options.emptyInventory ? [] : services }
          : url.endsWith("/servers")
            ? { servers: options.emptyInventory ? [] : servers }
            : { environments },
      ),
    );
  });
  vi.stubGlobal("fetch", fetch);
  render(
    <MemoryRouter>
      <EnvironmentsView />
    </MemoryRouter>,
  );
  return fetch;
}

test("create selects multiple services and servers, retaining selections through filters", async () => {
  const fetch = setup();
  await screen.findByText("No environments yet");
  await userEvent.click(screen.getByRole("button", { name: "Add environment" }));
  fireEvent.change(screen.getByLabelText("Environment name"), { target: { value: "Development" } });
  fireEvent.change(screen.getByLabelText("Description (optional)"), { target: { value: "Daily testing" } });
  for (const name of ["API", "Database", "App host", "Data host"])
    await userEvent.click(screen.getByRole("checkbox", { name }));
  fireEvent.change(screen.getByRole("textbox", { name: "Filter environment services" }), {
    target: { value: "nothing" },
  });
  fireEvent.change(screen.getByRole("textbox", { name: "Filter environment servers" }), {
    target: { value: "nothing" },
  });
  expect(screen.getByText("No services match. Your selections are kept.")).toBeInTheDocument();
  expect(screen.getByText("No servers match. Your selections are kept.")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Save environment" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Environment “Development” saved.");
  const request = fetch.mock.calls.find(([, init]) => init?.method === "POST")!;
  expect(JSON.parse(request[1]!.body as string)).toEqual({
    name: "Development",
    description: "Daily testing",
    service_ids: ["service-0", "service-1"],
    server_ids: ["server-0", "server-1"],
  });
  expect(
    within(screen.getByRole("region", { name: "Environment services" })).getByText("Database"),
  ).toBeInTheDocument();
  expect(
    within(screen.getByRole("region", { name: "Environment servers" })).getByText(/host1.internal/),
  ).toBeInTheDocument();
});

test("edit restores membership and explicitly removes links with the saved revision", async () => {
  const fetch = setup([{ ...development }]);
  await userEvent.click(await screen.findByRole("button", { name: "Edit Development" }));
  expect(screen.getByLabelText("Environment name")).toHaveValue("Development");
  for (const name of ["API", "Database", "App host", "Data host"])
    expect(screen.getByRole("checkbox", { name })).toBeChecked();
  await userEvent.click(screen.getByRole("checkbox", { name: "Database" }));
  await userEvent.click(screen.getByRole("checkbox", { name: "Data host" }));
  await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await screen.findByRole("status");
  const request = fetch.mock.calls.find(([, init]) => init?.method === "PUT")!;
  expect(request[0]).toBe("/api/environments/development");
  expect(JSON.parse(request[1]!.body as string)).toMatchObject({
    revision: 3,
    service_ids: ["service-0"],
    server_ids: ["server-0"],
  });
  await waitFor(() => expect(screen.getByRole("button", { name: "Edit Development" })).toBeEnabled());
  await userEvent.click(screen.getByRole("button", { name: "Edit Development" }));
  expect(screen.getByRole("checkbox", { name: "Database" })).not.toBeChecked();
  expect(screen.getByRole("checkbox", { name: "API" })).toBeChecked();
});

test("stale edits keep the form and cancellation preserves saved membership", async () => {
  const fetch = setup([{ ...development }], { saveStatus: 409 });
  await userEvent.click(await screen.findByRole("button", { name: "Edit Development" }));
  fireEvent.change(screen.getByLabelText("Environment name"), { target: { value: "Unsaved" } });
  await userEvent.click(screen.getByRole("checkbox", { name: "Database" }));
  await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
  expect(await screen.findByText(/This environment changed/)).toBeInTheDocument();
  expect(screen.getByLabelText("Environment name")).toHaveValue("Unsaved");
  expect(screen.getByRole("checkbox", { name: "Database" })).not.toBeChecked();
  await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Edit Development" })).toBeEnabled());
  await userEvent.click(screen.getByRole("button", { name: "Edit Development" }));
  expect(screen.getByRole("checkbox", { name: "Database" })).toBeChecked();
  expect(fetch.mock.calls.filter(([, init]) => init?.method === "PUT")).toHaveLength(1);
});

test("deletion waits for confirmation and completion while preserving the Cancel path", async () => {
  const environments = [{ ...development }];
  let finish!: (response: Response) => void;
  const fetch = setup(environments, {
    onDelete: () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  });
  await userEvent.click(await screen.findByRole("button", { name: "Delete Development" }));
  expect(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Cancel" })).toHaveFocus();
  expect(screen.getByText(/Services, servers, and their deployment records are preserved/)).toBeInTheDocument();
  await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Cancel" }));
  expect(fetch.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(false);
  await userEvent.click(screen.getByRole("button", { name: "Delete Development" }));
  await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete environment" }));
  expect(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Cancel" })).toBeDisabled();
  expect(fetch.mock.calls.find(([, init]) => init?.method === "DELETE")?.[0]).toBe(
    "/api/environments/development?revision=3&confirmed=true",
  );
  environments.splice(0);
  await act(async () => {
    finish(new Response(JSON.stringify({ id: development.id })));
  });
  expect(await screen.findByRole("status")).toHaveTextContent("Environment “Development” deleted.");
  expect(screen.queryByRole("alertdialog")).toBeNull();
  expect(screen.getByText("No environments yet")).toBeInTheDocument();
});

test("deletion failure retains the environment and explains the conflict", async () => {
  setup([{ ...development }], {
    onDelete: async () =>
      new Response(JSON.stringify({ error: { message: "This environment changed. Cancel, refresh, and try again" } }), {
        status: 409,
      }),
  });
  await userEvent.click(await screen.findByRole("button", { name: "Delete Development" }));
  await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete environment" }));
  expect(await within(screen.getByRole("alertdialog")).findByText(/This environment changed/)).toBeInTheDocument();
  expect(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Cancel" })).toBeEnabled();
  await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Cancel" }));
  expect(screen.getByRole("button", { name: "Edit Development" })).toBeInTheDocument();
});

test("list filtering includes members and hostnames", async () => {
  setup([{ ...development }]);
  await screen.findByRole("button", { name: "Details Development" });
  for (const query of ["Daily testing", "Database", "host1.internal"]) {
    fireEvent.change(screen.getByRole("textbox", { name: "Filter environments" }), { target: { value: query } });
    expect(screen.getByRole("button", { name: "Details Development" })).toBeInTheDocument();
  }
  fireEvent.change(screen.getByRole("textbox", { name: "Filter environments" }), { target: { value: "missing" } });
  expect(screen.getByText("No environments match this filter.")).toBeInTheDocument();
});

test("empty inventories allow an environment to be saved without members", async () => {
  const fetch = setup([], { emptyInventory: true });
  await userEvent.click(await screen.findByRole("button", { name: "Add your first environment" }));
  expect(screen.getByRole("link", { name: "Services page" })).toHaveAttribute("href", "/services");
  expect(screen.getByRole("link", { name: "Servers page" })).toHaveAttribute("href", "/servers");
  fireEvent.change(screen.getByLabelText("Environment name"), { target: { value: "Empty" } });
  await userEvent.click(screen.getByRole("button", { name: "Save environment" }));
  await screen.findByRole("status");
  const request = fetch.mock.calls.find(([, init]) => init?.method === "POST")!;
  expect(JSON.parse(request[1]!.body as string)).toMatchObject({ service_ids: [], server_ids: [] });
});

test("inventory errors disable creation and provide retry", async () => {
  setup([], { loadError: true });
  expect(await screen.findByText("Inventory unavailable")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Add environment" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Refresh" })).toBeEnabled();
});

test("an environment's name links to its page", async () => {
  setup([{ ...development }]);
  expect(await screen.findByRole("link", { name: "Development" })).toHaveAttribute("href", "/environments/development");
});

test("each environment row has icon buttons and a Documents link", async () => {
  setup([{ ...development }]);
  for (const name of ["Details", "Graph", "Edit", "Delete"])
    expect(await screen.findByRole("button", { name: `${name} Development` })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Documents Development" })).toHaveAttribute(
    "href",
    `/documents?environment_id=${development.id}`,
  );
});
