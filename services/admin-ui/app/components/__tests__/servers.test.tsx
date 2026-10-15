import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import ServersView from "~/features/servers/ServersView";
import type { ServerAsset } from "~/features/servers/api";

const server: ServerAsset = {
  id: "server-1",
  name: "App host",
  hostname: "app.internal",
  description: "Hosts payment workers",
  tags: ["production", "eu-west"],
  revision: 3,
  created_at: "2026-09-08T10:00:00Z",
  updated_at: "2026-09-08T10:00:00Z",
  ssh_port: 22,
  ssh_username: null,
  access_enabled: false,
  allow_sudo: false,
  ssh_allowed: false,
};

function setup(servers: ServerAsset[] = [], conflict = false) {
  const fetch = vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method) {
      if (conflict)
        return new Response(
          JSON.stringify({ error: { message: "This server changed. Cancel and reopen the form." } }),
          { status: 409 },
        );
      const saved = { ...server, ...JSON.parse(init.body as string), revision: 4 };
      servers = [saved];
      return new Response(JSON.stringify(saved), { status: init.method === "POST" ? 201 : 200 });
    }
    return new Response(JSON.stringify({ servers }));
  });
  vi.stubGlobal("fetch", fetch);
  render(
    <MemoryRouter>
      <ServersView />
    </MemoryRouter>,
  );
  return fetch;
}

/** The named icon button of a server row. */
async function iconButton(server: string, item: string) {
  return screen.findByRole("button", { name: `${item} ${server}` });
}

async function action(server: string, item: string) {
  await userEvent.setup().click(await iconButton(server, item));
}

test("server deletion opens a confirmation and Cancel leaves the server untouched", async () => {
  const fetch = setup([server]);
  await action("App host", "Delete");
  const dialog = within(screen.getByRole("alertdialog"));
  expect(dialog.getByRole("heading", { name: "Delete “App host”?" })).toBeInTheDocument();
  expect(dialog.getByText(/app.internal/)).toBeInTheDocument();
  expect(dialog.getByRole("button", { name: "Cancel" })).toHaveFocus();
  fireEvent.click(dialog.getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("alertdialog")).toBeNull();
  expect(fetch.mock.calls).toHaveLength(1);
  expect(screen.getByRole("button", { name: "Edit App host" })).toBeInTheDocument();
});

test("confirmed server deletion waits for the API then removes the row", async () => {
  let finish!: (value: Response) => void;
  let deleted = false;
  const fetch = vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === "DELETE") {
      deleted = true;
      return new Promise<Response>((resolve) => {
        finish = resolve;
      });
    }
    return new Response(JSON.stringify({ servers: deleted ? [] : [server] }));
  });
  vi.stubGlobal("fetch", fetch);
  render(
    <MemoryRouter>
      <ServersView />
    </MemoryRouter>,
  );
  await action("App host", "Delete");
  const dialog = within(screen.getByRole("alertdialog"));
  fireEvent.click(dialog.getByRole("button", { name: "Delete server" }));
  expect(dialog.getByRole("button", { name: /Delete server/ })).toBeDisabled();
  expect(dialog.getByRole("button", { name: "Cancel" })).toBeDisabled();
  expect(fetch.mock.calls[1]).toEqual(["/api/servers/server-1?revision=3&confirmed=true", { method: "DELETE" }]);
  await act(async () => {
    finish(new Response(JSON.stringify({ id: server.id })));
  });
  expect(await screen.findByText("Server “App host” deleted.")).toBeInTheDocument();
  expect(screen.queryByRole("alertdialog")).toBeNull();
  expect(screen.queryByRole("button", { name: "Edit App host" })).toBeNull();
});

test("a failed deletion keeps the dialog and server available", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: string, init?: RequestInit) =>
      init?.method === "DELETE"
        ? new Response(JSON.stringify({ error: { message: "Remove linked service deployments first" } }), {
            status: 409,
          })
        : new Response(JSON.stringify({ servers: [server] })),
    ),
  );
  render(
    <MemoryRouter>
      <ServersView />
    </MemoryRouter>,
  );
  await action("App host", "Delete");
  const dialog = within(screen.getByRole("alertdialog"));
  fireEvent.click(dialog.getByRole("button", { name: "Delete server" }));
  expect(await dialog.findByText("Remove linked service deployments first")).toBeInTheDocument();
  expect(dialog.getByRole("button", { name: "Cancel" })).toBeEnabled();
  fireEvent.click(dialog.getByRole("button", { name: "Cancel" }));
  expect(screen.getByRole("button", { name: "Edit App host" })).toBeInTheDocument();
});

test("register a server with description and several tags then display it", async () => {
  const fetch = setup();
  fireEvent.click(await screen.findByRole("button", { name: "Add your first server" }));
  const dialog = within(screen.getByRole("dialog"));
  fireEvent.change(dialog.getByLabelText("Server name"), { target: { value: "App host" } });
  fireEvent.change(dialog.getByLabelText("IP address or hostname"), { target: { value: "app.internal" } });
  fireEvent.change(dialog.getByLabelText("Description (optional)"), { target: { value: "Hosts payment workers" } });
  fireEvent.change(dialog.getByLabelText("Tags (optional)"), { target: { value: "production, eu-west, " } });
  expect(dialog.getByRole("switch", { name: "SSH allowed" })).not.toBeChecked();
  fireEvent.click(dialog.getByRole("switch", { name: "SSH allowed" }));
  fireEvent.click(dialog.getByRole("button", { name: "Register server" }));
  await screen.findByRole("status");
  const call = fetch.mock.calls.find(([, init]) => init?.method === "POST")!;
  expect(call[0]).toBe("/api/servers");
  expect(JSON.parse(call[1]!.body as string)).toEqual({
    name: "App host",
    hostname: "app.internal",
    description: "Hosts payment workers",
    tags: ["production", "eu-west"],
    ssh_allowed: true,
  });
  expect(await screen.findByText("eu-west")).toBeInTheDocument();
  expect(screen.getByText("On")).toBeInTheDocument();
  expect(screen.getByText("Hosts payment workers")).toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole("button", { name: "Add server" })).toBeEnabled());
});

test("search includes tags, descriptions, and hostnames", async () => {
  setup([server]);
  const filter = await screen.findByRole("textbox", { name: "Filter servers" });
  for (const query of ["EU-West", "payment", "app.internal"]) {
    fireEvent.change(filter, { target: { value: query } });
    expect(screen.getByRole("button", { name: "Edit App host" })).toBeInTheDocument();
  }
  fireEvent.change(filter, { target: { value: "missing" } });
  expect(screen.getByText("No servers match this filter.")).toBeInTheDocument();
});

test("edit preserves identity and revision, allowing description and tags to be cleared", async () => {
  const fetch = setup([{ ...server, ssh_allowed: true }]);
  await action("App host", "Edit");
  expect(screen.getByRole("switch", { name: "SSH allowed" })).toBeChecked();
  fireEvent.click(screen.getByRole("switch", { name: "SSH allowed" }));
  expect(screen.getByLabelText("Tags (optional)")).toHaveValue("production, eu-west");
  fireEvent.change(screen.getByLabelText("Tags (optional)"), { target: { value: "" } });
  fireEvent.change(screen.getByLabelText("Description (optional)"), { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await screen.findByRole("status");
  const call = fetch.mock.calls.find(([, init]) => init?.method === "PUT")!;
  expect(call[0]).toBe("/api/servers/server-1");
  expect(JSON.parse(call[1]!.body as string)).toEqual({
    name: "App host",
    hostname: "app.internal",
    description: "",
    tags: [],
    ssh_allowed: false,
    revision: 3,
  });
  expect(await screen.findByText("No tags")).toBeInTheDocument();
  expect(screen.getByText("Off")).toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole("button", { name: "Add server" })).toBeEnabled());
});

test("conflicts retain unsaved input; cancel reloads before reopening", async () => {
  const fetch = setup([server], true);
  await action("App host", "Edit");
  fireEvent.change(screen.getByLabelText("Description (optional)"), { target: { value: "Unsaved description" } });
  fireEvent.click(screen.getByRole("switch", { name: "SSH allowed" }));
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  expect(await screen.findByText("This server changed. Cancel and reopen the form.")).toBeInTheDocument();
  expect(screen.getByLabelText("Description (optional)")).toHaveValue("Unsaved description");
  expect(screen.getByRole("switch", { name: "SSH allowed" })).toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(fetch.mock.calls.filter(([, init]) => !init?.method)).toHaveLength(2));
  expect(await iconButton("App host", "Edit")).toBeEnabled();
});

test("failed inventory load prevents adding and can be retried", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ error: { message: "Database unavailable" } }), { status: 503 }),
      )
      .mockResolvedValueOnce(new Response(JSON.stringify({ servers: [] }))),
  );
  render(
    <MemoryRouter>
      <ServersView />
    </MemoryRouter>,
  );
  expect(await screen.findByText("Database unavailable")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Add server" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
  expect(await screen.findByText("Build your server inventory")).toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole("button", { name: "Add server" })).toBeEnabled());
});

test("Test SSH runs a connection test with the saved server settings and shows the result", async () => {
  const sshSettings = {
    id: "server-1",
    name: "App host",
    hostname: "app.internal",
    port: 2222,
    username: "deploy",
    revision: 5,
    ssh_allowed: true,
    enabled: true,
    allow_sudo: false,
    known_hosts: "",
    has_private_key: true,
    last_tested_at: null,
    last_test_error: null,
  };
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/servers/server-1/ssh/test")
      return new Response(
        JSON.stringify({ ok: true, message: "SSH connected. Host verification is off.", host_key: null }),
      );
    if (url === "/api/servers/server-1/ssh") return new Response(JSON.stringify(sshSettings));
    return new Response(JSON.stringify({ servers: [{ ...server, ssh_allowed: true }] }));
  });
  vi.stubGlobal("fetch", fetch);
  render(
    <MemoryRouter>
      <ServersView />
    </MemoryRouter>,
  );
  await action("App host", "Test SSH");
  expect(await screen.findByText("SSH connected. Host verification is off.")).toBeInTheDocument();
  const [, init] = fetch.mock.calls.find(([url]) => url === "/api/servers/server-1/ssh/test")!;
  expect(JSON.parse(init!.body as string)).toEqual({
    revision: 5,
    username: "deploy",
    port: 2222,
    allow_sudo: false,
    known_hosts: "",
  });
});

test("Test SSH shows why a server cannot connect", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url === "/api/servers/server-1/ssh/test")
        return new Response(JSON.stringify({ error: { message: "Set an SSH login user before testing." } }), {
          status: 409,
        });
      if (url === "/api/servers/server-1/ssh")
        return new Response(
          JSON.stringify({
            id: "server-1",
            port: 22,
            username: null,
            revision: 2,
            ssh_allowed: true,
            enabled: false,
            allow_sudo: false,
            known_hosts: "",
            has_private_key: true,
          }),
        );
      return new Response(JSON.stringify({ servers: [{ ...server, ssh_allowed: true }] }));
    }),
  );
  render(
    <MemoryRouter>
      <ServersView />
    </MemoryRouter>,
  );
  await action("App host", "Test SSH");
  expect(await screen.findByText(/Set an SSH login user before testing\./)).toBeInTheDocument();
  expect(screen.getByText(/App host: SSH test failed/)).toBeInTheDocument();
});

test("Test SSH is unavailable when SSH is not allowed", async () => {
  setup([server]);
  expect(await iconButton("App host", "Test SSH")).toBeDisabled();
});

test("the server name links to the server page", async () => {
  setup([server]);
  expect(await screen.findByRole("link", { name: "App host" })).toHaveAttribute("href", "/servers/server-1");
});

test("each server row has small icon buttons and a Documents link", async () => {
  setup([{ ...server, ssh_allowed: true }]);
  for (const name of ["SSH settings", "Test SSH", "Edit", "Delete"])
    expect(await iconButton("App host", name)).toBeEnabled();
  expect(screen.getByRole("link", { name: "Documents App host" })).toHaveAttribute(
    "href",
    "/documents?server_id=server-1",
  );
  expect(screen.queryByRole("button", { name: "Actions for App host" })).toBeNull();
});
