import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { renderRoutes } from "~/lib/__tests__/dataRouter";
import userEvent from "@testing-library/user-event";
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
  address: null,
  status: "confirmed",
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
  status: "confirmed",
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
  status: "confirmed",
  revision: 3,
  created_at: "2026-09-13T00:00:00Z",
  updated_at: "2026-09-13T00:00:00Z",
  services: services.map(({ id, name, status }) => ({ id, name, status })),
  servers: servers.map(({ id, name, hostname, status }) => ({ id, name, hostname, status })),
};

/** The environments page in a data router (its form's leave guard needs one), with a page to leave to. */
const page = () =>
  renderRoutes(
    [
      { path: "/environments", element: <EnvironmentsView /> },
      { path: "/services", element: <p>Services page</p> },
    ],
    "/environments",
  ).router;

afterEach(() => vi.unstubAllGlobals());

test("leaving with unsaved changes in the environment form asks Stay / Leave; an untouched form does not", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({ environments: [] }))),
  );
  const router = page();
  await userEvent.click(await screen.findByRole("button", { name: "Add environment" }));
  await userEvent.click(screen.getByRole("link", { name: "Services page" }));
  expect(await screen.findByText("Services page")).toBeInTheDocument();
  expect(screen.queryByRole("alertdialog")).toBeNull();
  await router.navigate("/environments");
  await userEvent.click(await screen.findByRole("button", { name: "Add environment" }));
  await userEvent.type(screen.getByLabelText("Environment name"), "QA");
  await userEvent.click(screen.getByRole("link", { name: "Services page" }));
  const dialog = within(await screen.findByRole("alertdialog"));
  await userEvent.click(dialog.getByRole("button", { name: "Stay" }));
  expect(router.state.location.pathname).toBe("/environments");
  expect(screen.getByLabelText("Environment name")).toHaveValue("QA");
  await userEvent.click(screen.getByRole("link", { name: "Services page" }));
  await userEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Leave" }));
  expect(await screen.findByText("Services page")).toBeInTheDocument();
});

function setup(
  environments: EnvironmentAsset[] = [],
  options: {
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
      };
      environments.splice(0, environments.length, saved);
      const { services: _services, servers: _servers, ...environment } = saved; // a write answers the bare item
      return new Response(JSON.stringify(environment), { status: init.method === "POST" ? 201 : 200 });
    }
    if (options.loadError)
      return new Response(JSON.stringify({ error: { message: "Inventory unavailable" } }), { status: 503 });
    return new Response(JSON.stringify({ environments }));
  });
  vi.stubGlobal("fetch", fetch);
  page();
  return fetch;
}

test("create sends the name and description only; contents come from deployments", async () => {
  const fetch = setup();
  await screen.findByText("No environments yet");
  await userEvent.click(screen.getByRole("button", { name: "Add environment" }));
  expect(screen.queryByRole("checkbox")).toBeNull();
  expect(screen.getByRole("link", { name: "Services page" })).toHaveAttribute("href", "/services");
  fireEvent.change(screen.getByLabelText("Environment name"), { target: { value: "Development" } });
  fireEvent.change(screen.getByLabelText("Description (optional)"), { target: { value: "Daily testing" } });
  await userEvent.click(screen.getByRole("button", { name: "Save environment" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Environment “Development” saved.");
  const request = fetch.mock.calls.find(([, init]) => init?.method === "POST")!;
  expect(JSON.parse(request[1]!.body as string)).toEqual({ name: "Development", description: "Daily testing" });
  // the details show what is deployed in it, read again after the save
  expect(
    await within(screen.getByRole("region", { name: "Environment services" })).findByText("Database"),
  ).toBeInTheDocument();
  expect(
    within(screen.getByRole("region", { name: "Environment servers" })).getByText(/host1.internal/),
  ).toBeInTheDocument();
});

test("edit sends the saved revision", async () => {
  const fetch = setup([{ ...development }]);
  await userEvent.click(await screen.findByRole("button", { name: "Edit Development" }));
  expect(screen.getByLabelText("Environment name")).toHaveValue("Development");
  fireEvent.change(screen.getByLabelText("Environment name"), { target: { value: "Dev" } });
  await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await screen.findByRole("status");
  const request = fetch.mock.calls.find(([, init]) => init?.method === "PUT")!;
  expect(request[0]).toBe("/api/environments/development");
  expect(JSON.parse(request[1]!.body as string)).toEqual({ name: "Dev", description: "Daily testing", revision: 3 });
});

test("stale edits keep the form", async () => {
  const fetch = setup([{ ...development }], { saveStatus: 409 });
  await userEvent.click(await screen.findByRole("button", { name: "Edit Development" }));
  fireEvent.change(screen.getByLabelText("Environment name"), { target: { value: "Unsaved" } });
  await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
  expect(await screen.findByText(/This environment changed/)).toBeInTheDocument();
  expect(screen.getByLabelText("Environment name")).toHaveValue("Unsaved");
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
  expect(screen.getByText(/An environment with deployments cannot be deleted/)).toBeInTheDocument();
  await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Cancel" }));
  expect(fetch.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(false);
  await userEvent.click(screen.getByRole("button", { name: "Delete Development" }));
  await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete environment" }));
  expect(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Cancel" })).toBeDisabled();
  expect(fetch.mock.calls.find(([, init]) => init?.method === "DELETE")?.[0]).toBe(
    "/api/environments/development?revision=3",
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

test("list filtering includes deployed services and hostnames", async () => {
  setup([{ ...development }]);
  await screen.findByRole("button", { name: "Details Development" });
  for (const query of ["Daily testing", "Database", "host1.internal"]) {
    fireEvent.change(screen.getByRole("textbox", { name: "Filter environments" }), { target: { value: query } });
    expect(screen.getByRole("button", { name: "Details Development" })).toBeInTheDocument();
  }
  fireEvent.change(screen.getByRole("textbox", { name: "Filter environments" }), { target: { value: "missing" } });
  expect(screen.getByText("No environments match this filter.")).toBeInTheDocument();
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
  for (const name of ["Details", "Edit", "Delete"])
    expect(await screen.findByRole("button", { name: `${name} Development` })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Map Development" })).toHaveAttribute(
    "href",
    "/system-map?map=infrastructure&root=environment%3Adevelopment&depth=2",
  );
  expect(screen.queryByRole("tab", { name: "Dependency graph" })).toBeNull();
  expect(screen.getByRole("link", { name: "Documents Development" })).toHaveAttribute(
    "href",
    `/documents?environment_id=${development.id}`,
  );
});

test("a draft environment shows a Draft badge with Confirm and Discard; Confirm reloads the list", async () => {
  const user = userEvent.setup();
  let current: EnvironmentAsset[] = [{ ...development, status: "draft" }];
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${url}`;
      calls.push(key);
      if (key === "GET /api/environments") return new Response(JSON.stringify({ environments: current }));
      if (key === "POST /api/inventory/environment/development/confirm?dry_run=1")
        return new Response(
          JSON.stringify({ confirmed: [{ kind: "environment", id: "development", name: "Development" }] }),
        );
      if (key === "POST /api/inventory/environment/development/confirm") {
        current = [{ ...development, status: "confirmed" }];
        return new Response(JSON.stringify({ confirmed: [] }));
      }
      throw new Error(`unexpected ${key}`);
    }),
  );
  page();
  const row = (await screen.findByRole("link", { name: "Development" })).closest("tr")!;
  expect(within(row).getByText("Draft")).toBeInTheDocument();
  expect(within(row).getByRole("button", { name: "Discard Development" })).toBeInTheDocument();
  await user.click(within(row).getByRole("button", { name: "Confirm Development" }));
  await user.click(await screen.findByRole("button", { name: "Confirm" }));
  await waitFor(() => expect(screen.queryByText("Draft")).not.toBeInTheDocument());
  expect(calls.filter((key) => key === "GET /api/environments")).toHaveLength(2);
});
