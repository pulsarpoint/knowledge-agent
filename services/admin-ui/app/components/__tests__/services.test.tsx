import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import ServicesView from "~/features/servers/ServicesView";
import type { EnvironmentAsset } from "~/features/servers/environments";
import type { Repository } from "~/features/codeProjects/api";
import type { ServerAsset } from "~/features/servers/api";
import type { ServiceAsset } from "~/features/servers/services";
import type { Connection, Deployment, Relation } from "~/features/servers/inventory";

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

const environments: EnvironmentAsset[] = ["production", "staging"].map((name, index) => ({
  id: index === 0 ? "env-prod" : "env-stage",
  name,
  description: "",
  project_id: "project-1",
  status: "confirmed",
  revision: 1,
  created_at: "",
  updated_at: "",
  services: [],
  servers: [],
}));

const savedDeployment: Deployment = {
  id: "deployment-1",
  service_id: "service-1",
  server_id: server.id,
  environment_id: "env-prod",
  runtime: "Docker",
  runtime_name: "payments-api",
  port: 8080,
  log_location: "container stdout",
  deployed_version: "v1.2",
  status: "confirmed",
  revision: 2,
};
const builtFrom = (repo: string, id: string): Relation => ({
  id,
  from_service_id: "service-1",
  kind: "built_from",
  to_service_id: null,
  to_code_project_id: repo,
  description: "",
  status: "confirmed",
  revision: 1,
});

/** The services page against a fake API. Writes answer the saved item: the sent body with an id and a revision;
 * `fail(url, init)` may answer a write with an error instead. */
function setup(
  services: ServiceAsset[] = [],
  options: {
    noRepositories?: boolean;
    noEnvironments?: boolean;
    initialEntry?: string;
    onDelete?: () => Promise<Response>;
    deployments?: Deployment[];
    relations?: Relation[];
    connections?: Connection[];
    fail?: (url: string, init: RequestInit) => Response | undefined;
  } = {},
) {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    if (method !== "GET") {
      const failed = options.fail?.(url, init!);
      if (failed) return failed;
    }
    if (method === "DELETE" && options.onDelete) return options.onDelete();
    if (method === "DELETE") return new Response(JSON.stringify({ id: url.split("/").pop()!.split("?")[0] }));
    if (method === "POST" && url.endsWith("/servers"))
      return new Response(JSON.stringify({ ...server, id: "new-server" }), { status: 201 });
    if (method === "POST" || method === "PUT") {
      const body = JSON.parse(init!.body as string);
      const kind = url.split("/")[2];
      const id = method === "PUT" ? url.split("/")[3] : kind === "services" ? "service-1" : `new-${kind}`;
      return new Response(JSON.stringify({ ...body, id, revision: (body.revision ?? 0) + 1 }), {
        status: method === "POST" ? 201 : 200,
      });
    }
    return new Response(
      JSON.stringify(
        url.endsWith("/deployments")
          ? { deployments: options.deployments ?? [] }
          : url.endsWith("/relations")
            ? { relations: options.relations ?? [] }
            : url.endsWith("/connections")
              ? { connections: options.connections ?? [] }
              : url.endsWith("/repositories")
                ? { repositories: options.noRepositories ? [] : repos }
                : url.endsWith("/storage")
                  ? {
                      locations: [
                        {
                          id: "code",
                          container_path: "/work/data/sources/code",
                          host_path: "/opt/ka/sources/code",
                          variable: "CODE_FOLDER",
                          status: "available",
                        },
                      ],
                    }
                  : url.endsWith("/servers")
                    ? { servers: [server] }
                    : url.endsWith("/environments")
                      ? { environments: options.noEnvironments ? [] : environments }
                      : { services },
      ),
    );
  });
  vi.stubGlobal("fetch", fetch);
  render(
    <MemoryRouter initialEntries={[options.initialEntry ?? "/services"]}>
      <ServicesView />
    </MemoryRouter>,
  );
  return fetch;
}

/** The writes sent, in order: [method, url, body]. */
const writes = (fetch: ReturnType<typeof setup>) =>
  fetch.mock.calls
    .filter(([, init]) => init?.method && init.method !== "GET")
    .map(([url, init]) => [init!.method, url, init!.body ? JSON.parse(init!.body as string) : undefined]);

test("service deletion asks for confirmation and Cancel preserves the service", async () => {
  const fetch = setup([service]);
  fireEvent.click(await screen.findByRole("button", { name: "Delete Payments" }));
  const dialog = screen.getByRole("alertdialog");
  expect(within(dialog).getByRole("heading", { name: "Delete “Payments”?" })).toBeInTheDocument();
  expect(within(dialog).getByText(/Running applications, servers, and code files are not changed/)).toBeInTheDocument();
  expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveFocus();
  fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("alertdialog")).toBeNull();
  expect(screen.getByRole("button", { name: "Delete Payments" })).toBeInTheDocument();
  expect(fetch.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(false);
});

test("deletion sends the revision, waits for success and removes the row and its details", async () => {
  let finish!: (response: Response) => void;
  const services = [service];
  const fetch = setup(services, {
    onDelete: () =>
      new Promise<Response>((resolve) => {
        finish = resolve;
      }),
  });
  fireEvent.click(await screen.findByRole("button", { name: "Details Payments" }));
  expect(screen.getByRole("button", { name: "Close service details" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Delete Payments" }));
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
  expect(await screen.findByRole("status")).toHaveTextContent("Service “Payments” deleted.");
  expect(screen.queryByRole("alertdialog")).toBeNull();
  expect(screen.queryByRole("button", { name: "Close service details" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Delete Payments" })).toBeNull();
  expect(await screen.findByText("Build your service inventory")).toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole("button", { name: "Add service" })).toBeEnabled());
});

test("failed deletion keeps the dialog and service available", async () => {
  setup([service], {
    onDelete: async () =>
      new Response(JSON.stringify({ error: { message: "This service changed. Cancel, refresh, and try again." } }), {
        status: 409,
      }),
  });
  fireEvent.click(await screen.findByRole("button", { name: "Delete Payments" }));
  fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete service" }));
  expect(
    await within(screen.getByRole("alertdialog")).findByText("This service changed. Cancel, refresh, and try again."),
  ).toBeInTheDocument();
  expect(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Cancel" })).toBeEnabled();
  fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Cancel" }));
  expect(screen.getByRole("button", { name: "Delete Payments" })).toBeInTheDocument();
});

test("create saves the service, then a built_from relation per code project", async () => {
  const fetch = setup();
  await screen.findByText("Build your service inventory");
  fireEvent.click(screen.getByRole("button", { name: "Add service" }));
  fireEvent.change(screen.getByLabelText("Service name"), { target: { value: "Payments" } });
  expect(screen.queryByLabelText(/Behavior and investigation notes/)).toBeNull();
  expect(screen.queryByRole("button", { name: "Environments" })).toBeNull();
  fireEvent.click(screen.getByRole("checkbox", { name: "API" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Shared" }));
  fireEvent.click(screen.getByRole("button", { name: "Save service" }));
  await screen.findByRole("status");
  const built = (repo: string) => ({
    from_service_id: "service-1",
    kind: "built_from",
    to_service_id: null,
    to_code_project_id: repo,
    description: "",
  });
  expect(writes(fetch)).toEqual([
    [
      "POST",
      "/api/services",
      {
        name: "Payments",
        service_type: "custom",
        service_role: "application",
        owner: "",
        description: "",
        address: "",
      },
    ],
    ["POST", "/api/relations", built("repo-0")],
    ["POST", "/api/relations", built("repo-1")],
  ]);
  await waitFor(() => expect(screen.getByRole("button", { name: "Add service" })).toBeEnabled());
});

test("edit loads deployments and relations, updates only what changed and removes an unchecked code project", async () => {
  const fetch = setup([service], {
    deployments: [savedDeployment],
    relations: [builtFrom("repo-0", "relation-0"), builtFrom("repo-1", "relation-1")],
  });
  fireEvent.click(await screen.findByRole("button", { name: "Edit Payments" }));
  expect(await screen.findByRole("combobox", { name: "Environment" })).toHaveTextContent("production");
  expect(screen.getByRole("checkbox", { name: "API" })).toBeChecked();
  fireEvent.click(screen.getByRole("checkbox", { name: "Shared" }));
  fireEvent.change(screen.getByLabelText("Deployed version (optional)"), { target: { value: "v1.3" } });
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await screen.findByRole("status");
  expect(writes(fetch)).toEqual([
    [
      "PUT",
      "/api/services/service-1",
      {
        name: "Payments",
        service_type: "custom",
        service_role: "application",
        description: "Handles payments",
        owner: "Backend",
        address: "",
        revision: 3,
      },
    ],
    ["PUT", "/api/deployments/deployment-1", { deployed_version: "v1.3", revision: 2 }],
    ["DELETE", "/api/relations/relation-1?revision=1", undefined],
  ]);
  await waitFor(() => expect(screen.getByRole("button", { name: "Edit Payments" })).toBeEnabled());
});

test("removing a saved deployment asks first, naming it and its connections, then deletes it", async () => {
  const connection = (id: string, from: string, to: string): Connection => ({
    id,
    from_deployment_id: from,
    to_deployment_id: to,
    to_service_id: null,
    to_host: null,
    port: 5432,
    protocol: "tcp",
    description: "",
    status: "confirmed",
    revision: 1,
  });
  const fetch = setup([service], {
    deployments: [savedDeployment],
    connections: [connection("c1", "deployment-1", "pg"), connection("c2", "worker", "deployment-1")],
  });
  fireEvent.click(await screen.findByRole("button", { name: "Edit Payments" }));
  fireEvent.click(await screen.findByRole("button", { name: "Remove deployment 1" }));
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  const dialog = await screen.findByRole("alertdialog");
  expect(
    within(dialog).getByRole("heading", {
      name: "Remove payments-api on App host (production) and its 2 connections?",
    }),
  ).toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
  expect(writes(fetch)).toEqual([]); // nothing is saved without confirming
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Remove and save" }));
  await screen.findByRole("status");
  expect(writes(fetch).slice(1)).toEqual([["DELETE", "/api/deployments/deployment-1?revision=2", undefined]]);
});

test("revision conflicts keep the unsaved form intact", async () => {
  setup([service], {
    fail: (url) =>
      url.startsWith("/api/services/")
        ? new Response(JSON.stringify({ error: { message: "This service changed. Refresh and reopen the form." } }), {
            status: 409,
          })
        : undefined,
  });
  fireEvent.click(await screen.findByRole("button", { name: "Edit Payments" }));
  fireEvent.change(await screen.findByLabelText("Service name"), { target: { value: "My unsaved name" } });
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  expect(await screen.findByText("This service changed. Refresh and reopen the form.")).toBeInTheDocument();
  expect(screen.getByLabelText("Service name")).toHaveValue("My unsaved name");
  expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled();
});

test("a failed write keeps the form open and saving again applies only the rest", async () => {
  let failing = true;
  const fetch = setup([postgres], {
    fail: (url) =>
      failing && url === "/api/relations"
        ? new Response(
            JSON.stringify({ error: { message: "Choose an existing service to relate to in this project" } }),
            {
              status: 400,
            },
          )
        : undefined,
  });
  await screen.findByRole("button", { name: "Edit PostgreSQL" });
  fireEvent.click(screen.getByRole("button", { name: "Add service" }));
  fireEvent.change(screen.getByLabelText("Service name"), { target: { value: "Payments" } });
  fireEvent.click(
    within(screen.getByRole("group", { name: "Depends on" })).getByRole("checkbox", { name: "PostgreSQL" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Save service" }));
  expect(await screen.findByText("Choose an existing service to relate to in this project")).toBeInTheDocument();
  failing = false;
  fireEvent.click(screen.getByRole("button", { name: "Save service" }));
  await screen.findByRole("status");
  expect(writes(fetch).map(([method, url]) => `${method} ${url}`)).toEqual([
    "POST /api/services",
    "POST /api/relations",
    "PUT /api/services/service-1",
    "POST /api/relations",
  ]);
  expect(writes(fetch)[2][2]).toMatchObject({ revision: 1 });
});

test("server registration assigns the new server without submitting the service", async () => {
  const fetch = setup();
  await screen.findByText("Build your service inventory");
  fireEvent.click(screen.getByRole("button", { name: "Add service" }));
  fireEvent.click(screen.getByRole("button", { name: "Register server" }));
  const dialog = screen.getByRole("dialog");
  fireEvent.change(within(dialog).getByLabelText("Server name"), { target: { value: "App host" } });
  fireEvent.change(within(dialog).getByLabelText("IP address or hostname"), { target: { value: "app.internal" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Register server" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(fetch.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
  expect(screen.getByRole("combobox", { name: "Server" })).toHaveTextContent("App host");
  fireEvent.change(screen.getByLabelText("Service name"), { target: { value: "API" } });
  const user = userEvent.setup();
  await user.click(screen.getByRole("combobox", { name: "Environment" }));
  await user.click(screen.getByRole("option", { name: "staging" }));
  fireEvent.click(screen.getByRole("button", { name: "Save service" }));
  await screen.findByRole("status");
  expect(writes(fetch).find(([, url]) => url === "/api/deployments")).toEqual([
    "POST",
    "/api/deployments",
    {
      service_id: "service-1",
      server_id: "new-server",
      environment_id: "env-stage",
      runtime: "",
      runtime_name: "",
      port: null,
      log_location: "",
      deployed_version: "",
    },
  ]);
  await waitFor(() => expect(screen.getByRole("button", { name: "Add service" })).toBeEnabled());
});

test("missing server selection is explained before saving", async () => {
  const fetch = setup();
  await screen.findByText("Build your service inventory");
  fireEvent.click(screen.getByRole("button", { name: "Add service" }));
  fireEvent.change(screen.getByLabelText("Service name"), { target: { value: "API" } });
  fireEvent.click(screen.getByRole("button", { name: "Add deployment" }));
  const user = userEvent.setup();
  await user.click(screen.getByRole("combobox", { name: "Environment" }));
  await user.click(screen.getByRole("option", { name: "staging" }));
  fireEvent.click(screen.getByRole("button", { name: "Save service" }));
  expect(await screen.findByText("Choose a server for each deployment.")).toBeInTheDocument();
  expect(fetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
});

test("each deployment needs an environment before saving", async () => {
  const fetch = setup();
  await screen.findByText("Build your service inventory");
  fireEvent.click(screen.getByRole("button", { name: "Add service" }));
  fireEvent.change(screen.getByLabelText("Service name"), { target: { value: "API" } });
  fireEvent.click(screen.getByRole("button", { name: "Add deployment" }));
  const user = userEvent.setup();
  await user.click(screen.getByRole("combobox", { name: "Server" }));
  await user.click(screen.getByRole("option", { name: /App host/ }));
  fireEvent.click(screen.getByRole("button", { name: "Save service" }));
  expect(await screen.findByText("Choose an environment for each deployment.")).toBeInTheDocument();
  expect(fetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
});

test("details distinguish deployed version from source and expose scoped agent context", async () => {
  setup([service]);
  fireEvent.click(await screen.findByRole("button", { name: "Details Payments" }));
  expect(screen.queryByText("Behavior and investigation notes")).toBeNull();
  expect(screen.queryByText("No notes recorded.")).toBeNull();
  expect(screen.getByText("v1.2")).toBeInTheDocument();
  expect(screen.getAllByText("Not pulled")).toHaveLength(2);
  expect(screen.getByRole("link", { name: "Agent context (JSON)" })).toHaveAttribute(
    "href",
    "/api/services/service-1/context",
  );
  expect(screen.getByText(/Live health and agent monitoring are not connected yet/)).toBeInTheDocument();
  fireEvent.change(screen.getByRole("textbox", { name: "Filter services" }), { target: { value: "app.internal" } });
  expect(screen.getByRole("button", { name: "Edit Payments" })).toBeInTheDocument();
  fireEvent.change(screen.getByRole("textbox", { name: "Filter services" }), { target: { value: "unknown" } });
  expect(screen.getByText("No services match this filter.")).toBeInTheDocument();
});

test("empty repository inventory offers a route to connect source later", async () => {
  setup([], { noRepositories: true });
  await screen.findByText("Build your service inventory");
  fireEvent.click(screen.getByRole("button", { name: "Add service" }));
  expect(screen.getByRole("link", { name: "Code projects page" })).toHaveAttribute("href", "/code-projects");
});

test("service details support code projects without a Git remote", async () => {
  setup([{ ...service, repositories: [{ ...service.repositories[0], remote_url: null }] }]);
  fireEvent.click(await screen.findByRole("button", { name: "Details Payments" }));
  expect(screen.getByText("Local folders")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "API" })).toHaveAttribute("href", "/code-projects/repo-0");
  expect(screen.queryByText("Not pulled")).not.toBeInTheDocument();
});

test("service details show a checkout inside the configured code folder", async () => {
  setup([{ ...service, repositories: [{ ...service.repositories[0], checkout_path: "payments/api" }] }]);
  fireEvent.click(await screen.findByRole("button", { name: "Details Payments" }));
  expect(await screen.findByText("/work/data/sources/code/payments/api")).toBeInTheDocument();
  expect(screen.queryByText(/\/work\/data\/code\//)).not.toBeInTheDocument();
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
const reporting: ServiceAsset = {
  ...postgres,
  id: "reporting",
  name: "Reporting",
  service_type: "custom",
  service_role: "application",
};

test("empty environment inventory explains how to add one", async () => {
  setup([], { noEnvironments: true });
  fireEvent.click(await screen.findByRole("button", { name: "Add your first service" }));
  fireEvent.click(screen.getByRole("button", { name: "Add deployment" }));
  expect(screen.getByRole("combobox", { name: "Environment" })).toBeDisabled();
  expect(screen.getByRole("link", { name: "Environments page" })).toHaveAttribute("href", "/environments");
});

test("service classification loads saved values and submits changed type and role; Address only for external", async () => {
  const fetch = setup([postgres]);
  await userEvent.click(await screen.findByRole("button", { name: "Edit PostgreSQL" }));
  expect(screen.getByRole("combobox", { name: "Service type" })).toHaveTextContent("Built-in software");
  expect(screen.getByRole("combobox", { name: "Primary role" })).toHaveTextContent("Database");
  await userEvent.click(screen.getByRole("combobox", { name: "Service type" }));
  expect(screen.queryByLabelText("Address (optional)")).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("option", { name: "External service" }));
  await userEvent.type(screen.getByLabelText("Address (optional)"), "db.example.com");
  await userEvent.click(screen.getByRole("combobox", { name: "Primary role" }));
  await userEvent.click(screen.getByRole("option", { name: "Proxy" }));
  await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await screen.findByRole("status");
  expect(writes(fetch)).toEqual([
    [
      "PUT",
      "/api/services/postgres",
      {
        name: "PostgreSQL",
        service_type: "external",
        service_role: "proxy",
        description: "Stores application data",
        owner: "Backend",
        address: "db.example.com",
        revision: 3,
      },
    ],
  ]);
});

test("service list can be filtered by type or role", async () => {
  setup([service, postgres]);
  await screen.findByRole("button", { name: "Edit PostgreSQL" });
  for (const query of ["Built-in software", "Database"]) {
    fireEvent.change(screen.getByRole("textbox", { name: "Filter services" }), { target: { value: query } });
    expect(screen.getByRole("button", { name: "Edit PostgreSQL" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit Payments" })).toBeNull();
  }
});

const relation = (
  id: string,
  kind: "depends_on" | "part_of",
  from: string,
  to: string,
  description: string,
): Relation => ({
  id,
  from_service_id: from,
  kind,
  to_service_id: to,
  to_code_project_id: null,
  description,
  status: "confirmed",
  revision: 1,
});

test("create relates the new service in each direction and retains selections when filtered", async () => {
  const fetch = setup([postgres, reporting]);
  fireEvent.click(await screen.findByRole("button", { name: "Add service" }));
  fireEvent.change(screen.getByLabelText("Service name"), { target: { value: "Payments" } });
  const dependencies = within(screen.getByRole("group", { name: "Depends on" }));
  const dependents = within(screen.getByRole("group", { name: "Used by" }));
  fireEvent.click(dependencies.getByRole("checkbox", { name: "PostgreSQL" }));
  fireEvent.change(screen.getByLabelText("Why Payments depends on PostgreSQL (optional)"), {
    target: { value: "Stores payment history" },
  });
  fireEvent.click(dependents.getByRole("checkbox", { name: "Reporting" }));
  fireEvent.change(screen.getByLabelText("Why Reporting depends on Payments (optional)"), {
    target: { value: "Reads payments via API" },
  });
  fireEvent.click(within(screen.getByRole("group", { name: "Parts" })).getByRole("checkbox", { name: "Reporting" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Filter depends on services" }), {
    target: { value: "missing" },
  });
  expect(dependencies.getByText(/No services match/)).toBeInTheDocument();
  expect(dependencies.getByText(/1 selected/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Save service" }));
  await screen.findByRole("status");
  const {
    id: _1,
    revision: _2,
    status: _s1,
    ...dependsOn
  } = relation("", "depends_on", "service-1", "postgres", "Stores payment history");
  const {
    id: _3,
    revision: _4,
    status: _s2,
    ...usedBy
  } = relation("", "depends_on", "reporting", "service-1", "Reads payments via API");
  const { id: _5, revision: _6, status: _s3, ...part } = relation("", "part_of", "reporting", "service-1", "");
  expect(writes(fetch).slice(1)).toEqual([
    ["POST", "/api/relations", dependsOn],
    ["POST", "/api/relations", usedBy],
    ["POST", "/api/relations", part],
  ]);
});

test("saving says which drafts it confirms: a related draft service, a new deployment's draft membership", async () => {
  const user = userEvent.setup();
  const onHost = { ...service.deployments[0], membership_status: "draft" as const };
  setup([{ ...postgres, status: "draft", deployments: [onHost] }, reporting]);
  fireEvent.click(await screen.findByRole("button", { name: "Add service" }));
  fireEvent.change(screen.getByLabelText("Service name"), { target: { value: "Payments" } });
  expect(screen.queryByText(/Saving confirms/)).toBeNull();
  fireEvent.click(
    within(screen.getByRole("group", { name: "Depends on" })).getByRole("checkbox", { name: "PostgreSQL" }),
  );
  expect(screen.getByText("Saving confirms the drafts it uses: PostgreSQL.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Add deployment" }));
  await user.click(screen.getByRole("combobox", { name: "Server" }));
  await user.click(screen.getByRole("option", { name: /App host/ }));
  await user.click(screen.getByRole("combobox", { name: "Environment" }));
  await user.click(screen.getByRole("option", { name: "production" }));
  expect(
    screen.getByText("Saving confirms the drafts it uses: PostgreSQL, App host in production."),
  ).toBeInTheDocument();
});

test("edit loads both directions, excludes self and supports removal and reverse description changes", async () => {
  const fetch = setup([service, postgres, reporting], {
    relations: [
      relation("r-postgres", "depends_on", "service-1", "postgres", "Stores payments"),
      relation("r-reporting", "depends_on", "reporting", "service-1", "Reads payments"),
    ],
  });
  fireEvent.click(await screen.findByRole("button", { name: "Edit Payments" }));
  const dependencies = within(await screen.findByRole("group", { name: "Depends on" }));
  const dependents = within(screen.getByRole("group", { name: "Used by" }));
  expect(dependencies.queryByRole("checkbox", { name: "Payments" })).toBeNull();
  expect(dependents.queryByRole("checkbox", { name: "Payments" })).toBeNull();
  expect(dependencies.getByRole("checkbox", { name: "PostgreSQL" })).toBeChecked();
  expect(dependents.getByRole("checkbox", { name: "Reporting" })).toBeChecked();
  expect(screen.getByLabelText("Why Payments depends on PostgreSQL (optional)")).toHaveValue("Stores payments");
  fireEvent.click(dependencies.getByRole("checkbox", { name: "PostgreSQL" }));
  fireEvent.change(screen.getByLabelText("Why Reporting depends on Payments (optional)"), {
    target: { value: "Calls the reporting endpoint" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await screen.findByRole("status");
  expect(writes(fetch).slice(1)).toEqual([
    ["DELETE", "/api/relations/r-postgres?revision=1", undefined],
    ["PUT", "/api/relations/r-reporting", { description: "Calls the reporting endpoint", revision: 1 }],
  ]);
});

test("details display relationships and Cancel leaves saved relationships untouched", async () => {
  const linked = {
    ...service,
    dependencies: [{ service_id: "postgres", name: "PostgreSQL", description: "Stores payments" }],
    dependents: [{ service_id: "reporting", name: "Reporting", description: "Reads payments" }],
  };
  const fetch = setup([linked, postgres, reporting], {
    relations: [relation("r-reporting", "depends_on", "reporting", "service-1", "Reads payments")],
  });
  fireEvent.click(await screen.findByRole("button", { name: "Details Payments" }));
  expect(within(screen.getByRole("region", { name: "Depends on" })).getByText("Stores payments")).toBeInTheDocument();
  expect(within(screen.getByRole("region", { name: "Used by" })).getByText("Reads payments")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Edit Payments" }));
  fireEvent.click(
    within(await screen.findByRole("group", { name: "Used by" })).getByRole("checkbox", { name: "Reporting" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Edit Payments" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Edit Payments" }));
  expect(
    within(await screen.findByRole("group", { name: "Used by" })).getByRole("checkbox", { name: "Reporting" }),
  ).toBeChecked();
  expect(writes(fetch)).toEqual([]);
});

test("services remains an inventory page without a graph tab", async () => {
  setup([service], { initialEntry: "/services?view=graph&layout=tala" });
  expect(await screen.findByRole("button", { name: "Edit Payments" })).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Filter services" })).toBeInTheDocument();
  expect(screen.queryByRole("tab", { name: "Dependency graph" })).toBeNull();
  expect(screen.queryByRole("region", { name: "Service dependency graph" })).toBeNull();
});

test("a service's name links to its page", async () => {
  setup([service]);
  expect(await screen.findByRole("link", { name: "Payments" })).toHaveAttribute("href", "/services/service-1");
});

test("each service row has icon buttons and a Documents link", async () => {
  setup([service]);
  for (const name of ["Details", "Edit", "Delete"])
    expect(await screen.findByRole("button", { name: `${name} Payments` })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Documents Payments" })).toHaveAttribute(
    "href",
    `/documents?service_id=${service.id}`,
  );
});
