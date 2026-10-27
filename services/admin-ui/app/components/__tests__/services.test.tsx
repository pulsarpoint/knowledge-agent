import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import ServicesView from "~/features/servers/ServicesView";
import type { Repository } from "~/features/codeProjects/api";
import type { ServerAsset } from "~/features/servers/api";
import type { ServiceAsset } from "~/features/servers/services";
import { Toaster } from "~/components/ui/sonner";
import { menuItems, rowAction, rowButtons } from "~/lib/__tests__/rowMenu";

beforeEach(() => {
  render(<Toaster />);
});

const repos: Repository[] = ["API", "Shared"].map((name, index) => ({
  id: `repo-${index}`,
  name,
  remote_url: `https://github.com/example/${name.toLowerCase()}.git`,
  requested_ref: null,
  has_token: false,
  current_commit: null,
  checkout_path: null,
  synced_at: null,
  job_id: null,
  queued_at: null,
  sync_status: null,
  sync_error: null,
}));
const server: ServerAsset = {
  id: "server-1",
  name: "App host",
  hostname: "app.internal",
  description: "Application host",
  tags: ["production"],
  status: "confirmed",
  revision: 1,
  created_at: "2026-09-08T10:00:00Z",
  updated_at: "2026-09-08T10:00:00Z",
  ssh_port: 22,
  ssh_username: null,
  access_enabled: false,
  allow_sudo: false,
  ssh_allowed: false,
};
const service: ServiceAsset = {
  service_type: "custom",
  service_role: "application",
  id: "service-1",
  project_id: "project-1",
  name: "Payments",
  description: "Handles payments",
  owner: "Backend",
  address: null,
  status: "confirmed",
  revision: 3,
  created_at: "2026-09-08T10:00:00Z",
  updated_at: "2026-09-08T10:00:00Z",
  repositories: repos.map((repo) => ({ ...repo, archived: false })),
  dependencies: [],
  dependents: [],
  environments: [{ id: "env-prod", name: "production" }],
  deployments: [
    {
      id: "deployment-1",
      server_id: server.id,
      environment_id: "env-prod",
      environment: "production",
      runtime: "Docker",
      runtime_name: "payments-api",
      port: 8080,
      log_location: "container stdout",
      deployed_version: "v1.2",
      status: "confirmed",
      environment_status: "confirmed",
      membership_status: "confirmed",
      server,
    },
  ],
};

/** The services list against a fake API: GET answers the services; a DELETE answers `onDelete`, or the deleted id. */
function setup(
  services: ServiceAsset[] = [],
  options: { initialEntry?: string; onDelete?: () => Promise<Response> } = {},
) {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "DELETE")
      return options.onDelete ? options.onDelete() : new Response(JSON.stringify({ id: url.split("/").pop() }));
    return new Response(JSON.stringify({ services }));
  });
  vi.stubGlobal("fetch", fetch);
  render(
    <MemoryRouter initialEntries={[options.initialEntry ?? "/services"]}>
      <ServicesView />
    </MemoryRouter>,
  );
  return fetch;
}

afterEach(() => vi.unstubAllGlobals());

test("service deletion asks for confirmation and Cancel preserves the service", async () => {
  const fetch = setup([service]);
  await rowAction(userEvent, "Payments", "Delete");
  const dialog = screen.getByRole("alertdialog");
  expect(within(dialog).getByRole("heading", { name: "Delete “Payments”?" })).toBeInTheDocument();
  expect(within(dialog).getByText(/Running applications, servers, and code files are not changed/)).toBeInTheDocument();
  expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveFocus();
  fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("alertdialog")).toBeNull();
  expect(screen.getByRole("button", { name: "Actions for Payments" })).toBeInTheDocument();
  expect(fetch.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(false);
});

test("deletion sends the revision, waits for success and removes the row", async () => {
  let finish!: (response: Response) => void;
  const services = [service];
  const fetch = setup(services, {
    onDelete: () =>
      new Promise<Response>((resolve) => {
        finish = resolve;
      }),
  });
  await rowAction(userEvent, "Payments", "Delete");
  fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete service" }));
  expect(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Cancel" })).toBeDisabled();
  expect(within(screen.getByRole("alertdialog")).getByRole("button", { name: /Delete service/ })).toBeDisabled();
  expect(fetch.mock.calls.filter(([, init]) => init?.method === "DELETE")).toEqual([
    ["/api/services/service-1?revision=3", { method: "DELETE" }],
  ]);
  services.splice(0);
  await act(async () => {
    finish(new Response(JSON.stringify({ id: service.id })));
  });
  expect(await screen.findByText("Service “Payments” deleted.")).toBeInTheDocument();
  expect(screen.queryByRole("alertdialog")).toBeNull();
  expect(screen.queryByRole("button", { name: "Actions for Payments" })).toBeNull();
  expect(await screen.findByText("Build your service inventory")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Add your first service" })).toBeInTheDocument();
});

test("failed deletion keeps the dialog and service available", async () => {
  setup([service], {
    onDelete: async () =>
      new Response(JSON.stringify({ error: { message: "This service changed. Cancel, refresh, and try again." } }), {
        status: 409,
      }),
  });
  await rowAction(userEvent, "Payments", "Delete");
  fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete service" }));
  expect(
    await within(screen.getByRole("alertdialog")).findByText("This service changed. Cancel, refresh, and try again."),
  ).toBeInTheDocument();
  expect(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Cancel" })).toBeEnabled();
  fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Cancel" }));
  expect(screen.getByRole("button", { name: "Actions for Payments" })).toBeInTheDocument();
});

const postgres: ServiceAsset = {
  ...service,
  id: "postgres",
  name: "PostgreSQL",
  service_type: "builtin",
  service_role: "database",
  description: "Stores application data",
  repositories: [],
  deployments: [],
  dependencies: [],
  dependents: [],
};

test("service list can be filtered by type or role", async () => {
  setup([service, postgres]);
  await screen.findByRole("button", { name: "Actions for PostgreSQL" });
  for (const query of ["Built-in software", "Database"]) {
    fireEvent.change(screen.getByRole("textbox", { name: "Filter services" }), { target: { value: query } });
    expect(screen.getByRole("button", { name: "Actions for PostgreSQL" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Actions for Payments" })).toBeNull();
  }
});

test("services remains an inventory page without a graph tab", async () => {
  setup([service], { initialEntry: "/services?view=graph&layout=tala" });
  expect(await screen.findByRole("button", { name: "Actions for Payments" })).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Filter services" })).toBeInTheDocument();
  expect(screen.queryByRole("tab", { name: "Dependency graph" })).toBeNull();
  expect(screen.queryByRole("region", { name: "Service dependency graph" })).toBeNull();
});

test("a service's name opens its View, Edit opens its Edit page and New service opens /services/new", async () => {
  setup([service]);
  expect(await screen.findByRole("link", { name: "Payments" })).toHaveAttribute("href", "/services/service-1/view");
  expect(screen.getByRole("link", { name: "New service" })).toHaveAttribute("href", "/services/new");
  const row = screen.getByRole("link", { name: "Payments" }).closest("tr")!;
  expect(rowButtons(row)).toEqual(["Actions for Payments"]);
  await userEvent.setup().click(within(row).getByRole("button", { name: "Actions for Payments" }));
  expect(menuItems()).toEqual(["Documents", "Edit", "Delete"]);
  expect(screen.getByRole("menuitem", { name: "Edit" })).toHaveAttribute("href", "/services/service-1/edit");
  expect(screen.getByRole("menuitem", { name: "Documents" })).toHaveAttribute(
    "href",
    `/documents?service_id=${service.id}`,
  );
});

test("an empty inventory offers New service", async () => {
  setup([]);
  expect(await screen.findByRole("link", { name: "Add your first service" })).toHaveAttribute("href", "/services/new");
});
