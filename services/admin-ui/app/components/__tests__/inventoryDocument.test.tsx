import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import DocumentEditorView from "~/features/documents/DocumentEditorView";
import DocumentsView from "~/features/documents/DocumentsView";
import EntryDocuments from "~/features/servers/EntryDocuments";

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

const at = "2026-10-15T10:00:00Z";
const TAKEN = "Server web-1 already has an inventory document.";
const ONE = "An inventory document must be built for one server, service or environment.";
const resources = {
  code_projects: [{ id: "repo-1", name: "Payments code" }],
  services: [{ id: "svc-1", name: "Payments API" }],
  servers: [{ id: "s1", name: "web-1" }],
  deployments: [],
  environments: [],
};
const noIds = { environment_ids: [], code_project_ids: [], service_ids: [], server_ids: [], deployment_ids: [] };
const RESOURCE_DOCS = "GET /api/inventory/server/s1/documents";

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
  const router = createMemoryRouter(
    [
      { path: "/servers/:serverId", element: <EntryDocuments entry={{ kind: "server", id: "s1", name: "web-1" }} /> },
      { path: "/documents", element: <DocumentsView /> },
      { path: "/documents/new", element: <DocumentEditorView /> },
      { path: "/documents/:documentId", element: <DocumentEditorView /> },
    ],
    { initialEntries: [url] },
  );
  render(<RouterProvider router={router} />);
  return router;
}

const builtFor = (type: string, id: string, name: string) => ({
  type,
  id,
  name,
  connection: "built_for",
  added_how: "person",
  added_by: "ana@example.com",
  session_id: null,
  created_at: at,
});
const document = {
  id: "doc-1",
  slug: "web-1-overview",
  title: "web-1 overview",
  description: "",
  purpose: "reference",
  audience: "both",
  tags: [],
  code_project_ids: [],
  service_ids: [],
  server_ids: ["s1"],
  deployment_ids: [],
  environment_ids: [],
  revision: 2,
  version: 1,
  content_hash: "a",
  origin: "manual",
  from_draft: false,
  is_draft: false,
  published: true,
  inventory: false,
  created_at: at,
  updated_at: at,
  markdown: "# web-1",
  source_snapshot: { ...resources, servers: [] },
  versions: [{ version: 1, title: "web-1 overview", origin: "manual", created_at: at, from_draft: false, live: true }],
  drafts: [],
  resource_links: [builtFor("server", "s1", "web-1")],
};

describe("a resource page", () => {
  test("renders its inventory document with Open, and the documents list", async () => {
    const web1 = { type: "server", id: "s1", name: "web-1" };
    api({
      [RESOURCE_DOCS]: () => [
        200,
        {
          inventory_document: {
            id: "doc-1",
            title: "web-1 overview",
            markdown: "# web-1\n\nThe web server.",
            version: 3,
            is_draft: false,
            published: true,
          },
          documents: [
            {
              id: "doc-1",
              title: "web-1 overview",
              tags: ["ops"],
              description_short: "What runs on web-1.",
              inventory: true,
              connection: "built_for",
              resource: web1,
            },
            {
              id: "doc-2",
              title: "Disk runbook",
              tags: ["disk", "runbook"],
              description_short: "",
              inventory: false,
              connection: "built_for",
              resource: web1,
            },
            {
              id: "doc-3",
              title: "nginx notes",
              tags: [],
              description_short: "Proxy settings.",
              inventory: false,
              connection: "linked",
              resource: { type: "service", id: "svc-9", name: "nginx" },
            },
          ],
        },
      ],
    });
    show("/servers/s1");
    const inventory = await screen.findByRole("region", { name: "Inventory document" });
    expect(await within(inventory).findByText("web-1 overview")).toBeInTheDocument();
    expect(within(inventory).getByText(/The web server\./)).toBeInTheDocument();
    expect(within(inventory).getByRole("link", { name: "Open" })).toHaveAttribute("href", "/documents/doc-1");
    expect(within(inventory).queryByText("Draft — not published")).toBeNull();
    expect(screen.queryByText("No inventory document yet.")).toBeNull();
    expect(screen.queryByText("Other documents")).toBeNull();
    const list = screen.getByRole("list", { name: "Documents" });
    const rows = within(list).getAllByRole("listitem");
    expect(rows).toHaveLength(3);
    expect(within(rows[0]).getByRole("link", { name: "web-1 overview" })).toHaveAttribute("href", "/documents/doc-1");
    expect(within(rows[0]).getByText("Inventory")).toBeInTheDocument();
    expect(within(rows[0]).getByText("ops")).toBeInTheDocument();
    expect(within(rows[0]).getByText("What runs on web-1.")).toBeInTheDocument();
    expect(within(rows[0]).queryByText(/· web-1/)).toBeNull();
    expect(within(rows[1]).getByRole("link", { name: "Disk runbook" })).toHaveAttribute("href", "/documents/doc-2");
    expect(within(rows[1]).getByText("disk")).toBeInTheDocument();
    expect(within(rows[1]).getByText("runbook")).toBeInTheDocument();
    expect(within(rows[1]).queryByText("Inventory")).toBeNull();
    expect(within(rows[2]).getByRole("link", { name: "nginx notes" })).toHaveAttribute("href", "/documents/doc-3");
    expect(within(rows[2]).getByText("Proxy settings.")).toBeInTheDocument();
    expect(within(rows[2]).getByText("· nginx")).toBeInTheDocument();
  });

  test("an empty documents list says No documents yet.", async () => {
    api({ [RESOURCE_DOCS]: () => [200, { inventory_document: null, documents: [] }] });
    show("/servers/s1");
    expect(await screen.findByText("No documents yet.")).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Documents" })).toBeNull();
  });

  test("a brand-new inventory draft is shown as not published, with Open", async () => {
    api({
      [RESOURCE_DOCS]: () => [
        200,
        {
          inventory_document: {
            id: "d9",
            title: "web-1 overview",
            markdown: "# Draft",
            version: null,
            is_draft: true,
            published: false,
          },
          documents: [],
        },
      ],
    });
    show("/servers/s1");
    const inventory = await screen.findByRole("region", { name: "Inventory document" });
    expect(await within(inventory).findByText("Draft — not published")).toBeInTheDocument();
    expect(within(inventory).getByRole("link", { name: "Open" })).toHaveAttribute("href", "/documents/d9");
    expect(screen.queryByRole("link", { name: "New inventory document" })).toBeNull();
  });

  test("without one: No inventory document yet, and New inventory document opens New built for the resource", async () => {
    api({ [RESOURCE_DOCS]: () => [200, { inventory_document: null, documents: [] }] });
    show("/servers/s1");
    expect(await screen.findByText("No inventory document yet.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "New inventory document" })).toHaveAttribute(
      "href",
      "/documents/new?inventory=server&resource_id=s1",
    );
  });
});

describe("New inventory document", () => {
  test("the New page is built for the resource (locked) and Save creates a flagged draft", async () => {
    const calls = api({
      "GET /api/documentation/resources": () => [200, resources],
      "POST /api/documentation/drafts": () => [201, { id: "d5" }],
      "GET /api/documentation/documents/d5": () => [
        200,
        { ...document, id: "d5", is_draft: true, published: false, inventory: true },
      ],
      "GET /api/inventory/server/s1/documents": () => [200, { inventory_document: null, documents: [] }],
    });
    const router = show("/documents/new?inventory=server&resource_id=s1");
    expect(await screen.findByText("New inventory document for server web-1.")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "web-1 overview" } });
    fireEvent.change(screen.getByLabelText("Markdown"), { target: { value: "# web-1" } });
    fireEvent.mouseDown(screen.getByRole("tab", { name: "Linked resources" }), { button: 0, ctrlKey: false });
    const server = await screen.findByRole("checkbox", { name: "web-1" });
    expect(server).toBeChecked();
    expect(server).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/documents/d5"));
    expect(calls.find((c) => c.key === "POST /api/documentation/drafts")!.body).toEqual({
      slug: "web-1-overview",
      title: "web-1 overview",
      description: "",
      purpose: "explanation",
      audience: "both",
      tags: [],
      markdown: "# web-1",
      resource_ids: { ...noIds, server_ids: ["s1"] },
      inventory: true,
    });
  });

  test("a refusal (the resource got one meanwhile) is shown on the page, not under the name", async () => {
    api({
      "GET /api/documentation/resources": () => [200, resources],
      "POST /api/documentation/drafts": () => [409, { error: { message: TAKEN } }],
    });
    show("/documents/new?inventory=server&resource_id=s1");
    fireEvent.change(await screen.findByLabelText("Title"), { target: { value: "web-1 overview" } });
    fireEvent.change(screen.getByLabelText("Markdown"), { target: { value: "# web-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(TAKEN)).toBeInTheDocument();
    expect(screen.getByLabelText("Slug")).not.toHaveAttribute("aria-invalid");
  });
});

describe("the document page", () => {
  const openResources = () =>
    fireEvent.mouseDown(screen.getByRole("tab", { name: "Linked resources" }), { button: 0, ctrlKey: false });

  test("an inventory document: a badge naming the resource; Unmark keeps the links and offers Mark again", async () => {
    let marked = true;
    const calls = api({
      "GET /api/documentation/documents/doc-1": () => [200, { ...document, inventory: true }],
      "GET /api/documentation/resources": () => [200, resources],
      "PUT /api/documentation/documents/doc-1/inventory": (body) => {
        marked = body.inventory;
        return [200, { id: "doc-1", revision: 3, inventory: body.inventory }];
      },
      [RESOURCE_DOCS]: () => [
        200,
        { inventory_document: marked ? { id: "doc-1", title: "web-1 overview" } : null, documents: [] },
      ],
    });
    show("/documents/doc-1");
    expect(await screen.findByText("Inventory document of server web-1")).toBeInTheDocument();
    openResources();
    fireEvent.click(await screen.findByRole("button", { name: "Unmark" }));
    await waitFor(() =>
      expect(calls.find((c) => c.key === "PUT /api/documentation/documents/doc-1/inventory")?.body).toEqual({
        inventory: false,
        revision: 2,
      }),
    );
    expect(await screen.findByRole("button", { name: "Mark as inventory document" })).toBeInTheDocument();
    expect(screen.queryByText("Inventory document of server web-1")).toBeNull();
    expect(screen.getByRole("checkbox", { name: "web-1" })).toBeDisabled(); // the built-for link stays
    fireEvent.click(screen.getByRole("button", { name: "Mark as inventory document" }));
    await waitFor(() =>
      expect(calls.filter((c) => c.key === "PUT /api/documentation/documents/doc-1/inventory").at(-1)?.body).toEqual({
        inventory: true,
        revision: 3,
      }),
    );
    expect(await screen.findByText("Inventory document of server web-1")).toBeInTheDocument();
  });

  test("Mark is offered for a document built for one server without an inventory document; a refusal shows", async () => {
    api({
      "GET /api/documentation/documents/doc-1": () => [200, document],
      "GET /api/documentation/resources": () => [200, resources],
      [RESOURCE_DOCS]: () => [200, { inventory_document: null, documents: [] }],
      "PUT /api/documentation/documents/doc-1/inventory": () => [409, { error: { message: TAKEN } }],
    });
    show("/documents/doc-1?tab=resources");
    fireEvent.click(await screen.findByRole("button", { name: "Mark as inventory document" }));
    expect(await screen.findByText(TAKEN)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Unmark" })).toBeNull();
  });

  test("Mark is not offered when the resource already has an inventory document", async () => {
    const calls = api({
      "GET /api/documentation/documents/doc-1": () => [200, document],
      "GET /api/documentation/resources": () => [200, resources],
      [RESOURCE_DOCS]: () => [200, { inventory_document: { id: "doc-7", title: "Other" }, documents: [] }],
    });
    show("/documents/doc-1?tab=resources");
    expect(await screen.findByRole("checkbox", { name: "web-1" })).toBeDisabled();
    await waitFor(() => expect(calls.some((c) => c.key === RESOURCE_DOCS)).toBe(true));
    expect(screen.queryByRole("button", { name: "Mark as inventory document" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Unmark" })).toBeNull();
  });

  test.each([
    ["nothing", []],
    ["a code project", [builtFor("code_project", "repo-1", "Payments code")]],
    ["two resources", [builtFor("server", "s1", "web-1"), builtFor("service", "svc-1", "Payments API")]],
  ])("Mark is not offered for a document built for %s", async (_, links) => {
    const calls = api({
      "GET /api/documentation/documents/doc-1": () => [200, { ...document, resource_links: links }],
      "GET /api/documentation/resources": () => [200, resources],
    });
    show("/documents/doc-1?tab=resources");
    expect(await screen.findByRole("button", { name: "Save links" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Mark as inventory document" })).toBeNull();
    expect(calls.some((c) => c.key.startsWith("GET /api/inventory/"))).toBe(false);
  });

  test("a mark refused because it is not built for one resource shows the message", async () => {
    api({
      "GET /api/documentation/documents/doc-1": () => [200, document],
      "GET /api/documentation/resources": () => [200, resources],
      [RESOURCE_DOCS]: () => [200, { inventory_document: null, documents: [] }],
      "PUT /api/documentation/documents/doc-1/inventory": () => [409, { error: { message: ONE } }],
    });
    show("/documents/doc-1?tab=resources");
    fireEvent.click(await screen.findByRole("button", { name: "Mark as inventory document" }));
    expect(await screen.findByText(ONE)).toBeInTheDocument();
  });
});

describe("the Documents list", () => {
  const listed = {
    ...document,
    inventory: true,
    draft_count: 0,
  };
  test("an Inventory badge on inventory documents; Inventory documents only filters the list", async () => {
    const calls = api({
      "GET /api/documentation/documents?": () => [
        200,
        { documents: [listed, { ...listed, id: "doc-2", title: "Runbook", inventory: false }], total: 2 },
      ],
      "GET /api/documentation/documents?inventory=true": () => [200, { documents: [listed], total: 1 }],
      "GET /api/documentation/drafts?new=true": () => [200, { drafts: [] }],
      "GET /api/documentation/resources": () => [200, resources],
    });
    show("/documents");
    const inventoryRow = (await screen.findByRole("link", { name: "web-1 overview" })).closest("tr")!;
    expect(within(inventoryRow).getByText("Inventory")).toBeInTheDocument();
    const other = screen.getByRole("link", { name: "Runbook" }).closest("tr")!;
    expect(within(other).queryByText("Inventory")).toBeNull();
    fireEvent.click(screen.getByRole("checkbox", { name: "Inventory documents only" }));
    await waitFor(() =>
      expect(calls.some((c) => c.key === "GET /api/documentation/documents?inventory=true")).toBe(true),
    );
    await waitFor(() => expect(screen.queryByRole("link", { name: "Runbook" })).toBeNull());
  });
});
