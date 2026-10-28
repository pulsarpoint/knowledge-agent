import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { Toaster } from "~/components/ui/sonner";
import { documentRoute } from "~/lib/__tests__/pageRoutes";

// A live document's resource links: the Connections page's Resources section (rows, Remove, Add resource) and the
// badges in the document's header.
vi.mock("../Markdown", () => ({ default: ({ content }: { content: string }) => <article>{content}</article> }));
const NativeRequest = globalThis.Request;
beforeEach(() =>
  vi.stubGlobal(
    "Request",
    class extends NativeRequest {
      constructor(input: RequestInfo | URL, init?: RequestInit) {
        super(input, { ...init, signal: undefined });
      }
    },
  ),
);
afterEach(() => vi.unstubAllGlobals());

const at = "2026-10-12T10:00:00Z";
const when = new Date(at).toLocaleString();
const pickable = {
  environments: [
    { id: "env-1", name: "dev" },
    { id: "env-2", name: "prod" },
  ],
  servers: [{ id: "srv-1", name: "web-1", hostname: "web-1.internal" }],
  services: [
    { id: "svc-1", name: "Payments" },
    { id: "svc-2", name: "Billing" },
  ],
  code_projects: [{ id: "repo-1", name: "Shop" }],
  deployments: [{ id: "dep-1", name: "Payments on web-1 (dev)", service_id: "svc-1", server_id: "srv-1" }],
};
const link = (type: string, id: string, name: string, connection = "linked", extra = {}) => ({
  type,
  id,
  name,
  connection,
  added_how: "person",
  added_by: "bo@example.com",
  session_id: null,
  created_at: at,
  service_id: null,
  ...extra,
});
const builtFor = link("environment", "env-1", "dev", "built_for");
const service = link("service", "svc-1", "Payments");
const linkedNow = [
  builtFor,
  link("server", "srv-1", "web-1"),
  service,
  link("code_project", "repo-1", "Shop"),
  link("deployment", "dep-1", "Payments on web-1 (dev)", "linked", { service_id: "svc-1" }),
];
const document = {
  id: "doc-1",
  slug: "runbook",
  title: "Runbook",
  description: "",
  purpose: "runbook",
  audience: "both",
  tags: [],
  code_project_ids: [],
  service_ids: [],
  server_ids: [],
  deployment_ids: [],
  environment_ids: [],
  revision: 2,
  version: 1,
  content_hash: "a",
  origin: "manual",
  from_draft: true,
  created_at: at,
  updated_at: at,
  markdown: "# Runbook",
  source_snapshot: {},
  versions: [{ version: 1, title: "Runbook", origin: "manual", created_at: at, from_draft: true, live: true }],
  drafts: [],
  is_draft: false,
  published: true,
  resource_links: [builtFor, service],
};
const connections = (resources: unknown[], revision = 2) => ({
  revision,
  resources,
  depends_on: [],
  used_by: [],
  built_from: [],
});

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
      return new Response(JSON.stringify(value), { status });
    }),
  );
  return calls;
}
function show(url: string) {
  const router = createMemoryRouter([documentRoute], { initialEntries: [url] });
  render(
    <>
      <RouterProvider router={router} />
      <Toaster />
    </>,
  );
  return router;
}
const base = (links = [builtFor, service]) => ({
  "GET /api/documentation/documents/doc-1": (): [number, unknown] => [200, { ...document, resource_links: links }],
  "GET /api/documentation/resources": (): [number, unknown] => [200, pickable],
});
const resourcesTable = async () => within(await screen.findByRole("table", { name: "Resources" }));

test("Resources lists each link: type, the name linking to the resource's page, the connection and who added it when", async () => {
  api({
    ...base(linkedNow),
    "GET /api/documentation/documents/doc-1/connections": () => [200, connections(linkedNow)],
  });
  show("/documents/doc-1/connections");
  const table = await resourcesTable();
  const rows = table.getAllByRole("row").slice(1);
  expect(rows.map((row) => within(row).getAllByRole("cell")[0].textContent)).toEqual([
    "Environment",
    "Server",
    "Service",
    "Code project",
    "Deployment",
  ]);
  const href = (name: string) => table.getByRole("link", { name }).getAttribute("href");
  expect(href("dev")).toBe("/environments/env-1");
  expect(href("web-1")).toBe("/servers/srv-1");
  expect(href("Payments")).toBe("/services/svc-1/view");
  expect(href("Shop")).toBe("/code-projects/repo-1/overview");
  expect(href("Payments on web-1 (dev)")).toBe("/services/svc-1/view");
  expect(within(rows[0]).getByText("Built for")).toBeInTheDocument();
  expect(within(rows[1]).getByText("Linked")).toBeInTheDocument();
  expect(rows[1]).toHaveTextContent(`bo@example.com · ${when}`);
  // the Resources section comes first
  const headings = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
  expect(headings[0]).toBe("Resources");
});

test("a document linked to nothing says so", async () => {
  api({
    ...base([]),
    "GET /api/documentation/documents/doc-1/connections": () => [200, connections([])],
  });
  show("/documents/doc-1/connections");
  expect(await screen.findByText("Not linked to any resource yet.")).toBeInTheDocument();
  expect(screen.queryByRole("table", { name: "Resources" })).toBeNull();
});

test("Remove asks first, then removes the link; a built-for link has no Remove, only why", async () => {
  const user = userEvent.setup();
  const calls = api({
    ...base(),
    "GET /api/documentation/documents/doc-1/connections": () => [200, connections([builtFor, service])],
    "DELETE /api/documentation/documents/doc-1/resources/service/svc-1": () => [200, connections([builtFor], 3)],
  });
  show("/documents/doc-1/connections");
  const table = await resourcesTable();
  await user.click(table.getByRole("button", { name: "Actions for environment dev" }));
  const off = screen.getByRole("menuitem", { name: "Remove" });
  expect(off).toHaveAttribute("aria-disabled", "true");
  expect(off).toHaveAccessibleDescription("Built for this resource: the link stays.");
  await user.keyboard("{Escape}");
  await user.click(table.getByRole("button", { name: "Actions for service Payments" }));
  await user.click(screen.getByRole("menuitem", { name: "Remove" }));
  const dialog = within(await screen.findByRole("alertdialog"));
  expect(dialog.getByText("Remove the link to service Payments?")).toBeInTheDocument();
  expect(dialog.getByText("The document and the service stay.")).toBeInTheDocument();
  expect(calls.some((c) => c.key.startsWith("DELETE"))).toBe(false);
  await user.click(dialog.getByRole("button", { name: "Remove link" }));
  await screen.findByText("Removed the link to service Payments.");
  await waitFor(() => expect(table.queryByRole("link", { name: "Payments" })).toBeNull());
  // the header's badges follow
  expect(screen.queryByRole("link", { name: "Service · Payments" })).toBeNull();
  expect(screen.getByRole("link", { name: "Environment · dev" })).toBeInTheDocument();
});

test("Add resource: a sheet searching every kind, grouped by type, without the linked ones; picking one links it", async () => {
  const user = userEvent.setup();
  const added = link("service", "svc-2", "Billing");
  const calls = api({
    ...base(),
    "GET /api/documentation/documents/doc-1/connections": () => [200, connections([builtFor, service])],
    "POST /api/documentation/documents/doc-1/resources": () => [201, connections([builtFor, service, added], 3)],
  });
  show("/documents/doc-1/connections");
  await resourcesTable();
  await user.click(screen.getByRole("button", { name: "Add resource" }));
  const sheet = within(await screen.findByRole("dialog", { name: "Add resource" }));
  const groups = sheet.getAllByRole("group").map((group) => group.getAttribute("aria-label"));
  expect(groups).toEqual(["Environments", "Servers", "Services", "Code projects", "Deployments"]);
  const options = (name: string) =>
    within(sheet.getByRole("group", { name }))
      .getAllByRole("button")
      .map((button) => button.textContent);
  expect(options("Environments")).toEqual(["prod"]); // dev is linked
  expect(options("Services")).toEqual(["Billing"]); // Payments is linked
  await user.type(sheet.getByRole("searchbox", { name: "Search resources" }), "bill");
  expect(sheet.getAllByRole("group").map((group) => group.getAttribute("aria-label"))).toEqual(["Services"]);
  await user.click(sheet.getByRole("button", { name: "Billing" }));
  await waitFor(() =>
    expect(calls.find((c) => c.key === "POST /api/documentation/documents/doc-1/resources")?.body).toEqual({
      type: "service",
      id: "svc-2",
    }),
  );
  await screen.findByText("Linked to service Billing.");
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "Add resource" })).toBeNull());
  expect((await resourcesTable()).getByRole("link", { name: "Billing" })).toBeInTheDocument();
});

test("the header shows a badge per link, linking to the resource; past four, +N links to Connections", async () => {
  const six = [...linkedNow, link("service", "svc-2", "Billing")];
  api({ ...base(six), "GET /api/documentation/documents/doc-1/connections": () => [200, connections(six)] });
  show("/documents/doc-1");
  const badges = within(await screen.findByRole("group", { name: "Linked resources" }));
  expect(badges.getAllByRole("link").map((item) => item.textContent)).toEqual([
    "Environment · dev",
    "Server · web-1",
    "Service · Billing",
    "Service · Payments",
    "+2",
  ]);
  expect(badges.getByRole("link", { name: "Environment · dev" })).toHaveAttribute("href", "/environments/env-1");
  expect(badges.getByRole("link", { name: "+2" })).toHaveAttribute("href", "/documents/doc-1/connections");
});

test("a live document has no Linked resources tab in the editor: its links are on Connections", async () => {
  api(base());
  show("/documents/doc-1");
  await screen.findByRole("tab", { name: "Write" });
  expect(screen.queryByRole("tab", { name: "Linked resources" })).toBeNull();
  expect(screen.getByRole("link", { name: "Environment · dev" })).toBeInTheDocument();
});

test("the same service and server in two environments read differently in the rows, the badges and the Add sheet", async () => {
  const user = userEvent.setup();
  const onDev = link("deployment", "dep-1", "Payments on web-1 (dev)", "linked", { service_id: "svc-1" });
  const onProd = link("deployment", "dep-2", "Payments on web-1 (prod)", "linked", { service_id: "svc-1" });
  api({
    ...base([onDev]),
    "GET /api/documentation/resources": () => [
      200,
      {
        ...pickable,
        deployments: [
          ...pickable.deployments,
          { id: "dep-2", name: "Payments on web-1 (prod)", service_id: "svc-1", server_id: "srv-1" },
          { id: "dep-3", name: "Payments on web-1 (staging)", service_id: "svc-1", server_id: "srv-1" },
        ],
      },
    ],
    "GET /api/documentation/documents/doc-1/connections": () => [200, connections([onDev, onProd])],
  });
  show("/documents/doc-1/connections");
  const table = await resourcesTable();
  expect(table.getByRole("link", { name: "Payments on web-1 (dev)" })).toBeInTheDocument();
  expect(table.getByRole("link", { name: "Payments on web-1 (prod)" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Deployment · Payments on web-1 (dev)" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Add resource" }));
  const sheet = within(await screen.findByRole("dialog", { name: "Add resource" }));
  expect(
    within(sheet.getByRole("group", { name: "Deployments" }))
      .getAllByRole("button")
      .map((button) => button.textContent),
  ).toEqual(["Payments on web-1 (staging)"]);
});

test("removing a service names the deployments that go with it", async () => {
  const user = userEvent.setup();
  const onDev = link("deployment", "dep-1", "Payments on web-1 (dev)", "linked", {
    service_id: "svc-1",
    server_id: "srv-1",
  });
  const calls = api({
    ...base([service, onDev]),
    "GET /api/documentation/documents/doc-1/connections": () => [200, connections([service, onDev])],
    "DELETE /api/documentation/documents/doc-1/resources/service/svc-1": () => [200, connections([], 3)],
  });
  show("/documents/doc-1/connections");
  const table = await resourcesTable();
  await user.click(table.getByRole("button", { name: "Actions for service Payments" }));
  await user.click(screen.getByRole("menuitem", { name: "Remove" }));
  const dialog = within(await screen.findByRole("alertdialog"));
  expect(
    dialog.getByText("Remove the link to service Payments and its deployment Payments on web-1 (dev)?"),
  ).toBeInTheDocument();
  await user.click(dialog.getByRole("button", { name: "Remove link" }));
  await screen.findByText("Not linked to any resource yet.");
  expect(calls.filter((c) => c.key.startsWith("DELETE"))).toHaveLength(1);
});

test("a refused Remove shows why in the dialog and keeps the link", async () => {
  const user = userEvent.setup();
  const REFUSED = "This document was built for deployment Payments on web-1 (dev); that link can't be removed.";
  api({
    ...base(),
    "GET /api/documentation/documents/doc-1/connections": () => [200, connections([builtFor, service])],
    "DELETE /api/documentation/documents/doc-1/resources/service/svc-1": () => [409, { error: { message: REFUSED } }],
  });
  show("/documents/doc-1/connections");
  const table = await resourcesTable();
  await user.click(table.getByRole("button", { name: "Actions for service Payments" }));
  await user.click(screen.getByRole("menuitem", { name: "Remove" }));
  const dialog = within(await screen.findByRole("alertdialog"));
  await user.click(dialog.getByRole("button", { name: "Remove link" }));
  expect(await dialog.findByText(REFUSED)).toBeInTheDocument();
  await user.click(dialog.getByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
  expect(table.getByRole("link", { name: "Payments" })).toBeInTheDocument();
});

test("Add resource: a refusal stays in the open sheet; a success updates the header badges; resources load once", async () => {
  const user = userEvent.setup();
  let tries = 0;
  const calls = api({
    ...base(),
    "GET /api/documentation/documents/doc-1/connections": () => [200, connections([builtFor, service])],
    "POST /api/documentation/documents/doc-1/resources": () =>
      tries++ === 0
        ? [409, { error: { message: "This document is already linked to that server." } }]
        : [201, connections([builtFor, link("server", "srv-1", "web-1"), service], 3)],
  });
  show("/documents/doc-1/connections");
  await resourcesTable();
  await user.click(screen.getByRole("button", { name: "Add resource" }));
  const sheet = within(await screen.findByRole("dialog", { name: "Add resource" }));
  await user.click(sheet.getByRole("button", { name: "web-1" }));
  expect(await sheet.findByText("This document is already linked to that server.")).toBeInTheDocument();
  expect(screen.getByRole("dialog", { name: "Add resource" })).toBeInTheDocument();
  await user.click(sheet.getByRole("button", { name: "web-1" }));
  await screen.findByText("Linked to server web-1.");
  expect(screen.getByRole("link", { name: "Server · web-1" })).toHaveAttribute("href", "/servers/srv-1");
  expect(calls.filter((c) => c.key === "GET /api/documentation/resources")).toHaveLength(1);
});

test("Connections shows the live version's documented source commits under Built from", async () => {
  const snapshot = { code_projects: [{ id: "repo-1", name: "Shop", git_commit: "abc123" }] };
  api({
    "GET /api/documentation/documents/doc-1": () => [200, { ...document, source_snapshot: snapshot }],
    "GET /api/documentation/resources": () => [200, pickable],
    "GET /api/documentation/documents/doc-1/connections": () => [200, connections([])],
  });
  show("/documents/doc-1/connections");
  const built = within(await screen.findByRole("region", { name: "Built from" }));
  expect(built.getByText("Documented source commits")).toBeInTheDocument();
  expect(built.getByText("abc123")).toBeInTheDocument();
  expect(built.getByText(/Shop/)).toBeInTheDocument();
});

test("a brand-new draft's ?tab=resources stays on its Linked resources tab, with no badges", async () => {
  api({
    "GET /api/documentation/documents/doc-1": () => [200, { ...document, is_draft: true, published: false }],
    "GET /api/documentation/resources": () => [200, pickable],
  });
  const router = show("/documents/doc-1?tab=resources");
  expect(await screen.findByRole("tab", { name: "Linked resources", selected: true })).toBeInTheDocument();
  expect(router.state.location.pathname).toBe("/documents/doc-1");
  expect(screen.queryByRole("group", { name: "Linked resources" })).toBeNull();
});
