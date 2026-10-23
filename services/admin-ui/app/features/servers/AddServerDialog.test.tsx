import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router";
import type { ServerAsset } from "./api";
import type { EnvironmentModel } from "./environments";
import AddServerDialog from "./AddServerDialog";
import EnvironmentWorkspace from "./EnvironmentWorkspace";

const asset = (id: string, name: string): ServerAsset => ({
  id,
  name,
  hostname: `${name}.internal`,
  description: "",
  tags: [],
  status: "confirmed",
  revision: 1,
  ssh_port: 22,
  ssh_username: null,
  access_enabled: false,
  allow_sudo: false,
  ssh_allowed: false,
  created_at: "2026-10-22T00:00:00Z",
  updated_at: "2026-10-22T00:00:00Z",
});
const servers = [asset("s1", "app-1"), asset("s2", "db-1"), asset("s3", "cache-1")];

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
      return status === 204 ? new Response(null, { status }) : new Response(JSON.stringify(value), { status });
    }),
  );
  return calls;
}
afterEach(() => vi.unstubAllGlobals());

function dialog(onAdded = vi.fn()) {
  render(<AddServerDialog environmentId="e1" memberIds={["s1"]} onAdded={onAdded} onClose={vi.fn()} />);
  return onAdded;
}

test("the picker offers the servers not in the environment, and a new one", async () => {
  const user = userEvent.setup();
  api({ "GET /api/servers": () => [200, { servers }] });
  dialog();
  await user.click(await screen.findByRole("combobox", { name: "Server" }));
  expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual([
    "cache-1 · cache-1.internal",
    "db-1 · db-1.internal",
    "A new server",
  ]);
});

test("adding an existing server puts it in the environment", async () => {
  const user = userEvent.setup();
  const calls = api({
    "GET /api/servers": () => [200, { servers }],
    "PUT /api/environments/e1/servers/s2": () => [204, null],
  });
  const onAdded = dialog();
  await user.click(await screen.findByRole("combobox", { name: "Server" }));
  await user.click(screen.getByRole("option", { name: "db-1 · db-1.internal" }));
  expect(screen.queryByLabelText("Hostname")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Add server" }));
  await vi.waitFor(() => expect(onAdded).toHaveBeenCalledWith("s2"));
  expect(calls.map((call) => call.key)).toEqual(["GET /api/servers", "PUT /api/environments/e1/servers/s2"]);
});

test("adding a new server creates it, then puts it in the environment", async () => {
  const user = userEvent.setup();
  const calls = api({
    "GET /api/servers": () => [200, { servers }],
    "POST /api/servers": (body) => [201, { ...asset("s9", body.name), hostname: body.hostname }],
    "PUT /api/environments/e1/servers/s9": () => [204, null],
  });
  const onAdded = dialog();
  await user.click(await screen.findByRole("combobox", { name: "Server" }));
  await user.click(screen.getByRole("option", { name: "A new server" }));
  await user.type(screen.getByLabelText("Name"), "web-2");
  await user.type(screen.getByLabelText("Hostname"), "10.0.0.9");
  await user.type(screen.getByLabelText("Description"), "Second web host");
  await user.click(screen.getByRole("button", { name: "Add server" }));
  await vi.waitFor(() => expect(onAdded).toHaveBeenCalledWith("s9"));
  expect(calls.find((call) => call.key === "POST /api/servers")!.body).toEqual({
    name: "web-2",
    hostname: "10.0.0.9",
    description: "Second web host",
    tags: [],
    ssh_allowed: false,
  });
  expect(calls.at(-1)!.key).toBe("PUT /api/environments/e1/servers/s9");
});

test("a refused save shows the message and keeps the dialog", async () => {
  const user = userEvent.setup();
  api({
    "GET /api/servers": () => [200, { servers }],
    "PUT /api/environments/e1/servers/s2": () => [404, { error: { message: "Server not found" } }],
  });
  const onAdded = dialog();
  await user.click(await screen.findByRole("combobox", { name: "Server" }));
  await user.click(screen.getByRole("option", { name: "db-1 · db-1.internal" }));
  await user.click(screen.getByRole("button", { name: "Add server" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Server not found");
  expect(onAdded).not.toHaveBeenCalled();
});

function Where() {
  return <output data-testid="where">{useLocation().search}</output>;
}

test("the workspace's Add server reloads the model and selects the server", async () => {
  const user = userEvent.setup();
  const empty: EnvironmentModel = {
    environment: { id: "e1", name: "prod", description: "", status: "confirmed", revision: 1 },
    servers: [],
    services: [],
    connections: [],
    relations: [],
    external: [],
    external_services: [],
    drafts: 0,
    document_drafts: 0,
  };
  let current = empty;
  const calls = api({
    "GET /api/environments/e1/model": () => [200, current],
    "GET /api/servers": () => [200, { servers }],
    "PUT /api/environments/e1/servers/s2": () => {
      current = {
        ...empty,
        servers: [
          {
            id: "s2",
            name: "db-1",
            hostname: "",
            description: "",
            tags: [],
            status: "confirmed",
            membership_status: "confirmed",
            revision: 1,
            deployments: [],
          },
        ],
      };
      return [204, null];
    },
  });
  render(
    <MemoryRouter initialEntries={["/environments/e1"]}>
      <EnvironmentWorkspace environmentId="e1" />
      <Where />
    </MemoryRouter>,
  );
  expect(await screen.findByText("No servers in this environment yet.")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Add server" }));
  await user.click(await screen.findByRole("combobox", { name: "Server" }));
  await user.click(screen.getByRole("option", { name: "db-1 · db-1.internal" }));
  await user.click(screen.getByRole("button", { name: "Add server" }));
  expect(await screen.findByRole("region", { name: "Services on db-1" })).toBeInTheDocument();
  expect(screen.getByTestId("where")).toHaveTextContent("?server=s2");
  expect(calls.filter((call) => call.key === "GET /api/environments/e1/model")).toHaveLength(2);
});

test("adding a draft server to a draft environment says saving confirms both", async () => {
  const user = userEvent.setup();
  api({ "GET /api/servers": () => [200, { servers: [servers[0], { ...servers[1], status: "draft" }, servers[2]] }] });
  render(<AddServerDialog environmentId="e1" memberIds={["s1"]} uses={["prod"]} onAdded={vi.fn()} onClose={vi.fn()} />);
  await user.click(await screen.findByRole("combobox", { name: "Server" }));
  await user.click(screen.getByRole("option", { name: "db-1 · db-1.internal" }));
  expect(screen.getByText("Saving confirms the drafts it uses: prod, db-1.")).toBeInTheDocument();
});
