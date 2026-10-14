import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import ServicesView from "~/features/servers/ServicesView";
import type { EnvironmentAsset } from "~/features/servers/environments";
import type { Repository } from "~/features/codeProjects/api";
import type { ServerAsset } from "~/features/servers/api";
import type { ServiceAsset } from "~/features/servers/services";

const repos: Repository[] = ["API", "Shared"].map((name, index) => ({
  id: `repo-${index}`, name, remote_url: `https://github.com/example/${name.toLowerCase()}.git`, requested_ref: null,
  has_token: false, current_commit: null, checkout_path: null, synced_at: null, job_id: null, queued_at: null, sync_status: null, sync_error: null,
}));
const server: ServerAsset = { id: "server-1", name: "App host", hostname: "app.internal", description: "Application host", tags: ["production"], revision: 1, created_at: "2026-09-08T10:00:00Z", updated_at: "2026-09-08T10:00:00Z", ssh_port: 22, ssh_username: null, access_enabled: false, allow_sudo: false, ssh_allowed: false };
const service: ServiceAsset = {
  service_type: "custom", service_role: "application",
  id: "service-1", project_id: "project-1", name: "Payments", description: "Handles payments", owner: "Backend", operations_notes: "Queue should drain within 30 seconds.",
  revision: 3, created_at: "2026-09-08T10:00:00Z", updated_at: "2026-09-08T10:00:00Z", repositories: repos.map((repo) => ({ ...repo, archived: false })),
  dependencies: [], dependents: [], environments: [{id:"env-prod",name:"production"}],
  deployments: [{ id: "deployment-1", server_id: server.id, environment_id: "env-prod", environment: "production", runtime: "Docker", runtime_name: "payments-api", port: 8080, log_location: "container stdout", deployed_version: "v1.2", server }],
};

const environments: EnvironmentAsset[] = ['production','staging'].map((name, index) => ({
  id: index === 0 ? 'env-prod' : 'env-stage', name, description:'', project_id:'project-1', revision:1,
  created_at:'', updated_at:'', services:[], servers:[],
}));

function setup(services: ServiceAsset[] = [], options: { saveStatus?: number; noRepositories?: boolean; noEnvironments?: boolean; initialEntry?: string; onDelete?: () => Promise<Response> } = {}) {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "DELETE" && options.onDelete) return options.onDelete();
    if (init?.method === "POST" && url.endsWith("/servers")) return new Response(JSON.stringify({ ...server, id: "new-server" }), { status: 201 });
    if (init?.method === "POST" || init?.method === "PUT") {
      return new Response(JSON.stringify(options.saveStatus ? { error: { message: "This service changed. Refresh and reopen the form." } } : { id: "service-1", revision: 4 }), { status: options.saveStatus ?? 201 });
    }
    return new Response(JSON.stringify(url.endsWith("/repositories") ? { repositories: options.noRepositories ? [] : repos }
      : url.endsWith("/storage") ? { locations: [{ id: "code", container_path: "/work/data/sources/code", host_path: "/opt/ka/sources/code", variable: "CODE_FOLDER", status: "available" }] }
      : url.endsWith("/servers") ? { servers: [server] } : url.endsWith("/environments") ? { environments: options.noEnvironments ? [] : environments } : { services }));
  });
  vi.stubGlobal("fetch", fetch);
  render(<MemoryRouter initialEntries={[options.initialEntry ?? "/services"]}><ServicesView /></MemoryRouter>);
  return fetch;
}

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
  const fetch = setup(services, { onDelete: () => new Promise<Response>((resolve) => { finish = resolve; }) });
  fireEvent.click(await screen.findByRole("button", { name: "View Payments" }));
  expect(screen.getByRole("button", { name: "Close service details" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Delete Payments" }));
  fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete service" }));
  expect(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Cancel" })).toBeDisabled();
  expect(within(screen.getByRole("alertdialog")).getByRole("button", { name: /Delete service/ })).toBeDisabled();
  expect(fetch.mock.calls.filter(([, init]) => init?.method === "DELETE")).toEqual([
    ["/api/services/service-1?revision=3&confirmed=true", { method: "DELETE" }],
  ]);
  services.splice(0);
  await act(async () => { finish(new Response(JSON.stringify({ id: service.id }))); });
  expect(await screen.findByRole("status")).toHaveTextContent("Service “Payments” deleted.");
  expect(screen.queryByRole("alertdialog")).toBeNull();
  expect(screen.queryByRole("button", { name: "Close service details" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Delete Payments" })).toBeNull();
  expect(await screen.findByText("Build your service inventory")).toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole("button", { name: "Add service" })).toBeEnabled());
});

test("failed deletion keeps the dialog and service available", async () => {
  setup([service], { onDelete: async () => new Response(JSON.stringify({ error: { message: "This service changed. Cancel, refresh, and try again." } }), { status: 409 }) });
  fireEvent.click(await screen.findByRole("button", { name: "Delete Payments" }));
  fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete service" }));
  expect(await within(screen.getByRole("alertdialog")).findByText("This service changed. Cancel, refresh, and try again.")).toBeInTheDocument();
  expect(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Cancel" })).toBeEnabled();
  fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Cancel" }));
  expect(screen.getByRole("button", { name: "Delete Payments" })).toBeInTheDocument();
});

test("create links several repositories without requiring known deployment details", async () => {
  const fetch = setup();
  await screen.findByText("Build your service inventory");
  fireEvent.click(screen.getByRole("button", { name: "Add service" }));
  fireEvent.change(screen.getByLabelText("Service name"), { target: { value: "Payments" } });
  fireEvent.change(screen.getByLabelText("Behavior and investigation notes (optional)"), { target: { value: "Normal latency below 200ms" } });
  fireEvent.click(screen.getByRole("checkbox", { name: "API" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Shared" }));
  fireEvent.click(screen.getByRole("button", { name: "Save service" }));
  await screen.findByRole("status");
  const request = fetch.mock.calls.find(([, init]) => init?.method === "POST")!;
  expect(JSON.parse(request[1]!.body as string)).toEqual({ name: "Payments", service_type: "custom", service_role: "application", owner: "", description: "", operations_notes: "Normal latency below 200ms", repository_ids: ["repo-0", "repo-1"], deployments: [], dependencies: [], dependents: [], environment_ids: [] });
  await waitFor(() => expect(screen.getByRole("button", { name: "Add service" })).toBeEnabled());
});

test("edit preserves deployment identity and revision while removing a repository link", async () => {
  const fetch = setup([service]);
  fireEvent.click(await screen.findByRole("button", { name: "Edit Payments" }));
  expect(screen.getByRole("combobox", {name:"Environment"})).toHaveTextContent("production");
  fireEvent.click(screen.getByRole("checkbox", { name: "Shared" }));
  fireEvent.change(screen.getByLabelText("Deployed version (optional)"), { target: { value: "v1.3" } });
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await screen.findByRole("status");
  const request = fetch.mock.calls.find(([, init]) => init?.method === "PUT")!;
  const value = JSON.parse(request[1]!.body as string);
  expect(value.revision).toBe(3);
  expect(value.repository_ids).toEqual(["repo-0"]);
  expect(value.deployments[0]).toMatchObject({ id: "deployment-1", server_id: "server-1", deployed_version: "v1.3" });
  expect(value.deployments[0]).not.toHaveProperty("server");
  expect(value.deployments[0]).not.toHaveProperty("key");
  await waitFor(() => expect(screen.getByRole("button", { name: "Edit Payments" })).toBeEnabled());
});

test("revision conflicts keep the unsaved form intact", async () => {
  setup([service], { saveStatus: 409 });
  fireEvent.click(await screen.findByRole("button", { name: "Edit Payments" }));
  fireEvent.change(screen.getByLabelText("Service name"), { target: { value: "My unsaved name" } });
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  expect(await screen.findByText("This service changed. Refresh and reopen the form.")).toBeInTheDocument();
  expect(screen.getByLabelText("Service name")).toHaveValue("My unsaved name");
  expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled();
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
  await user.click(screen.getByRole('combobox', {name:'Environment'}));
  await user.click(screen.getByRole('option', {name:'staging'}));
  fireEvent.click(screen.getByRole("button", { name: "Save service" }));
  await screen.findByRole("status");
  const request = fetch.mock.calls.find(([url, init]) => url.endsWith("/services") && init?.method === "POST")!;
  expect(JSON.parse(request[1]!.body as string).deployments[0]).toMatchObject({ server_id: "new-server", environment: "staging" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Add service" })).toBeEnabled());
});

test("missing server selection is explained before saving", async () => {
  const fetch = setup();
  await screen.findByText("Build your service inventory");
  fireEvent.click(screen.getByRole("button", { name: "Add service" }));
  fireEvent.change(screen.getByLabelText("Service name"), { target: { value: "API" } });
  fireEvent.click(screen.getByRole("button", { name: "Add deployment" }));
  const user = userEvent.setup();
  await user.click(screen.getByRole('combobox', {name:'Environment'}));
  await user.click(screen.getByRole('option', {name:'staging'}));
  fireEvent.click(screen.getByRole("button", { name: "Save service" }));
  expect(await screen.findByText("Choose a server for each deployment.")).toBeInTheDocument();
  expect(fetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
});

test("details distinguish deployed version from source and expose scoped agent context", async () => {
  setup([service]);
  fireEvent.click(await screen.findByRole("button", { name: "View Payments" }));
  expect(screen.getByText("Queue should drain within 30 seconds.")).toBeInTheDocument();
  expect(screen.getByText("v1.2")).toBeInTheDocument();
  expect(screen.getAllByText("Not pulled")).toHaveLength(2);
  expect(screen.getByRole("link", { name: "Agent context (JSON)" })).toHaveAttribute("href", "/api/services/service-1/context");
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
  fireEvent.click(await screen.findByRole("button", { name: "View Payments" }));
  expect(screen.getByText("Local folders")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "API" })).toHaveAttribute("href", "/code-projects");
  expect(screen.queryByText("Not pulled")).not.toBeInTheDocument();
});

test("service details show a checkout inside the configured code folder", async () => {
  setup([{ ...service, repositories: [{ ...service.repositories[0], checkout_path: "payments/api" }] }]);
  fireEvent.click(await screen.findByRole("button", { name: "View Payments" }));
  expect(await screen.findByText("/work/data/sources/code/payments/api")).toBeInTheDocument();
  expect(screen.queryByText(/\/work\/data\/code\//)).not.toBeInTheDocument();
});

const postgres: ServiceAsset = { ...service, id: "postgres", name: "PostgreSQL", service_type: "builtin", service_role: "database", description: "Stores application data", repositories: [], deployments: [], dependencies: [], dependents: [] };
const reporting: ServiceAsset = { ...postgres, id: "reporting", name: "Reporting", service_type: "custom", service_role: "application" };

test('selects multiple available environments without requiring a deployment', async () => {
  const user = userEvent.setup(); const fetch = setup();
  await screen.findByText('Build your service inventory');
  await user.click(screen.getByRole('button', {name:'Add service'}));
  await user.type(screen.getByLabelText('Service name'), 'Shared API');
  await user.click(screen.getByRole('button', {name:'Environments'}));
  await user.click(screen.getByRole('menuitemcheckbox', {name:'production'}));
  await user.click(screen.getByRole('menuitemcheckbox', {name:'staging'}));
  expect(screen.getByRole('menuitemcheckbox', {name:'production'})).toBeChecked();
  expect(screen.getByRole('menuitemcheckbox', {name:'staging'})).toBeChecked();
  await user.keyboard('{Escape}');
  expect(screen.getByRole('button', {name:'Environments'})).toHaveTextContent('2 environments selected');
  await user.click(screen.getByRole('button', {name:'Save service'}));
  await screen.findByRole('status');
  const body = JSON.parse(fetch.mock.calls.find(([,init]) => init?.method === 'POST')![1]!.body as string);
  expect(body.environment_ids).toEqual(['env-prod','env-stage']);
  expect(body.deployments).toEqual([]);
});

test('deployment environments stay selected until their deployment is removed', async () => {
  const user = userEvent.setup(); const fetch = setup([service]);
  await user.click(await screen.findByRole('button', {name:'Edit Payments'}));
  await user.click(screen.getByRole('button', {name:'Environments'}));
  expect(screen.getByRole('menuitemcheckbox', {name:'production'})).toBeChecked();
  expect(screen.getByRole('menuitemcheckbox', {name:'production'})).toHaveAttribute('aria-disabled', 'true');
  await user.keyboard('{Escape}');
  await user.click(screen.getByRole('button', {name:'Remove deployment 1'}));
  await user.click(screen.getByRole('button', {name:'Environments'}));
  await user.click(screen.getByRole('menuitemcheckbox', {name:'production'}));
  await user.keyboard('{Escape}');
  await user.click(screen.getByRole('button', {name:'Save changes'}));
  await screen.findByRole('status');
  const body = JSON.parse(fetch.mock.calls.find(([,init]) => init?.method === 'PUT')![1]!.body as string);
  expect(body.environment_ids).toEqual([]);
  expect(body.deployments).toEqual([]);
});

test('empty environment inventory explains how to add one', async () => {
  setup([], {noEnvironments:true});
  fireEvent.click(await screen.findByRole('button', {name:'Add your first service'}));
  expect(screen.getByRole('button', {name:'Environments'})).toBeDisabled();
  expect(screen.getByRole('link', {name:'Environments page'})).toHaveAttribute('href','/environments');
  fireEvent.click(screen.getByRole('button', {name:'Add deployment'}));
  expect(screen.getByRole('combobox', {name:'Environment'})).toBeDisabled();
});

test('legacy deployment labels remain visible until explicitly linked', async () => {
  const fetch = setup([{...service,environments:[],deployments:[{...service.deployments[0],environment_id:null,environment:'Old lab'}]}]);
  fireEvent.click(await screen.findByRole('button', {name:'Edit Payments'}));
  expect(screen.getByRole('combobox', {name:'Environment'})).toHaveTextContent('Old lab (unlinked label)');
  expect(screen.getByText(/This saved label is not linked/)).toBeVisible();
  fireEvent.click(screen.getByRole('button', {name:'Save changes'}));
  await screen.findByRole('status');
  const body = JSON.parse(fetch.mock.calls.find(([,init]) => init?.method === 'PUT')![1]!.body as string);
  expect(body.deployments[0]).toMatchObject({id:'deployment-1',environment:'Old lab',environment_id:null});
});

test("service classification loads saved values and submits changed type and role", async () => {
  const fetch = setup([postgres]);
  await userEvent.click(await screen.findByRole("button", { name: "Edit PostgreSQL" }));
  expect(screen.getByRole("combobox", { name: "Service type" })).toHaveTextContent("Built-in software");
  expect(screen.getByRole("combobox", { name: "Primary role" })).toHaveTextContent("Database");
  await userEvent.click(screen.getByRole("combobox", { name: "Service type" }));
  await userEvent.click(screen.getByRole("option", { name: "External service" }));
  await userEvent.click(screen.getByRole("combobox", { name: "Primary role" }));
  await userEvent.click(screen.getByRole("option", { name: "Proxy" }));
  await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await screen.findByRole("status");
  const request = fetch.mock.calls.find(([, init]) => init?.method === "PUT")!;
  expect(JSON.parse(request[1]!.body as string)).toMatchObject({ service_type: "external", service_role: "proxy", deployments: [], repository_ids: [] });
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

test("create selects both relationship directions and retains selections when filtered", async () => {
  const fetch = setup([postgres, reporting]);
  fireEvent.click(await screen.findByRole("button", { name: "Add service" }));
  fireEvent.change(screen.getByLabelText("Service name"), { target: { value: "Payments" } });
  const dependencies = within(screen.getByRole("group", { name: "Depends on" }));
  const dependents = within(screen.getByRole("group", { name: "Used by" }));
  fireEvent.click(dependencies.getByRole("checkbox", { name: "PostgreSQL" }));
  fireEvent.change(screen.getByLabelText("Why Payments depends on PostgreSQL (optional)"), { target: { value: "Stores payment history" } });
  fireEvent.click(dependents.getByRole("checkbox", { name: "Reporting" }));
  fireEvent.change(screen.getByLabelText("Why Reporting depends on Payments (optional)"), { target: { value: "Reads payments via API" } });
  fireEvent.change(screen.getByRole("textbox", { name: "Filter depends on services" }), { target: { value: "missing" } });
  expect(dependencies.getByText(/No services match/)).toBeInTheDocument();
  expect(dependencies.getByText(/1 selected/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Save service" }));
  await screen.findByRole("status");
  const request = fetch.mock.calls.find(([url, init]) => url.endsWith("/services") && init?.method === "POST")!;
  expect(JSON.parse(request[1]!.body as string)).toMatchObject({
    dependencies: [{ service_id: "postgres", description: "Stores payment history" }],
    dependents: [{ service_id: "reporting", description: "Reads payments via API" }],
  });
});

test("edit loads both directions, excludes self and supports removal and reverse description changes", async () => {
  const linked = { ...service, dependencies: [{ service_id: "postgres", name: "PostgreSQL", description: "Stores payments" }],
    dependents: [{ service_id: "reporting", name: "Reporting", description: "Reads payments" }] };
  const fetch = setup([linked, postgres, reporting]);
  fireEvent.click(await screen.findByRole("button", { name: "Edit Payments" }));
  const dependencies = within(screen.getByRole("group", { name: "Depends on" }));
  const dependents = within(screen.getByRole("group", { name: "Used by" }));
  expect(dependencies.queryByRole("checkbox", { name: "Payments" })).toBeNull();
  expect(dependents.queryByRole("checkbox", { name: "Payments" })).toBeNull();
  expect(dependencies.getByRole("checkbox", { name: "PostgreSQL" })).toBeChecked();
  expect(dependents.getByRole("checkbox", { name: "Reporting" })).toBeChecked();
  expect(screen.getByLabelText("Why Payments depends on PostgreSQL (optional)")).toHaveValue("Stores payments");
  fireEvent.click(dependencies.getByRole("checkbox", { name: "PostgreSQL" }));
  fireEvent.change(screen.getByLabelText("Why Reporting depends on Payments (optional)"), { target: { value: "Calls the reporting endpoint" } });
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await screen.findByRole("status");
  const request = fetch.mock.calls.find(([, init]) => init?.method === "PUT")!;
  expect(JSON.parse(request[1]!.body as string)).toMatchObject({ revision: service.revision, dependencies: [],
    dependents: [{ service_id: "reporting", description: "Calls the reporting endpoint" }] });
});

test("details display relationships and Cancel leaves saved relationships untouched", async () => {
  const linked = { ...service, dependencies: [{ service_id: "postgres", name: "PostgreSQL", description: "Stores payments" }],
    dependents: [{ service_id: "reporting", name: "Reporting", description: "Reads payments" }] };
  const fetch = setup([linked, postgres, reporting]);
  fireEvent.click(await screen.findByRole("button", { name: "View Payments" }));
  expect(within(screen.getByRole("region", { name: "Depends on" })).getByText("Stores payments")).toBeInTheDocument();
  expect(within(screen.getByRole("region", { name: "Used by" })).getByText("Reads payments")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Edit Payments" }));
  fireEvent.click(within(screen.getByRole("group", { name: "Used by" })).getByRole("checkbox", { name: "Reporting" }));
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Edit Payments" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Edit Payments" }));
  expect(within(screen.getByRole("group", { name: "Used by" })).getByRole("checkbox", { name: "Reporting" })).toBeChecked();
  expect(fetch.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(false);
});

test("services remains an inventory page without a graph tab", async () => {
  setup([service], { initialEntry: "/services?view=graph&layout=tala" });
  expect(await screen.findByRole("button", { name: "Edit Payments" })).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Filter services" })).toBeInTheDocument();
  expect(screen.queryByRole("tab", { name: "Dependency graph" })).toBeNull();
  expect(screen.queryByRole("region", { name: "Service dependency graph" })).toBeNull();
});
