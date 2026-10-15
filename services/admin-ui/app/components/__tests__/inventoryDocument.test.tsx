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
              tags: ["inventory", "ops"],
              description_short: "What runs on web-1.",
              connection: "built_for",
              resource: web1,
            },
            {
              id: "doc-2",
              title: "Disk runbook",
              tags: ["disk", "runbook"],
              description_short: "",
              connection: "built_for",
              resource: web1,
            },
            {
              id: "doc-3",
              title: "nginx notes",
              tags: [],
              description_short: "Proxy settings.",
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
    // every tag badge links to the Documents list filtered by that tag
    expect(within(rows[0]).getByRole("link", { name: "inventory" })).toHaveAttribute(
      "href",
      "/documents?tag=inventory",
    );
    expect(within(rows[0]).getByRole("link", { name: "ops" })).toHaveAttribute("href", "/documents?tag=ops");
    expect(within(rows[0]).getByText("What runs on web-1.")).toBeInTheDocument();
    expect(within(rows[0]).queryByText(/· web-1/)).toBeNull();
    expect(within(rows[1]).getByRole("link", { name: "Disk runbook" })).toHaveAttribute("href", "/documents/doc-2");
    expect(within(rows[1]).getByRole("link", { name: "disk" })).toHaveAttribute("href", "/documents?tag=disk");
    expect(within(rows[1]).getByRole("link", { name: "runbook" })).toHaveAttribute("href", "/documents?tag=runbook");
    expect(within(rows[1]).queryByText("inventory")).toBeNull();
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
  test("the New page is built for the resource (locked), its Tags start with inventory; Save sends the tag", async () => {
    const calls = api({
      "GET /api/documentation/resources": () => [200, resources],
      "POST /api/documentation/drafts": () => [201, { id: "d5" }],
      "GET /api/documentation/documents/d5": () => [
        200,
        { ...document, id: "d5", is_draft: true, published: false, tags: ["inventory"] },
      ],
    });
    const router = show("/documents/new?inventory=server&resource_id=s1");
    expect(await screen.findByText("New inventory document for server web-1.")).toBeInTheDocument();
    expect(screen.getByLabelText("Tags")).toHaveValue("inventory");
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
      tags: ["inventory"],
      markdown: "# web-1",
      resource_ids: { ...noIds, server_ids: ["s1"] },
    });
  });

  test("a plain New page starts with no tags", async () => {
    api({ "GET /api/documentation/resources": () => [200, resources] });
    show("/documents/new");
    expect(await screen.findByLabelText("Tags")).toHaveValue("");
  });
});

describe("the document page", () => {
  test("its tags are badges linking to the filtered list; there is no Mark or Unmark", async () => {
    const calls = api({
      "GET /api/documentation/documents/doc-1": () => [200, { ...document, tags: ["inventory", "ops"] }],
      "GET /api/documentation/resources": () => [200, resources],
    });
    show("/documents/doc-1?tab=resources");
    const tags = await screen.findByRole("group", { name: "Document tags" });
    expect(within(tags).getByRole("link", { name: "inventory" })).toHaveAttribute("href", "/documents?tag=inventory");
    expect(within(tags).getByRole("link", { name: "ops" })).toHaveAttribute("href", "/documents?tag=ops");
    expect(await screen.findByRole("checkbox", { name: "web-1" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Mark as inventory document" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Unmark" })).toBeNull();
    expect(calls.some((c) => c.key.endsWith("/inventory") || c.key.startsWith("GET /api/inventory/"))).toBe(false);
  });

  test("a document without tags shows no tag badges", async () => {
    api({
      "GET /api/documentation/documents/doc-1": () => [200, document],
      "GET /api/documentation/resources": () => [200, resources],
    });
    show("/documents/doc-1");
    expect(await screen.findByLabelText("Tags")).toHaveValue("");
    expect(screen.queryByRole("group", { name: "Document tags" })).toBeNull();
  });
});

describe("the Documents list", () => {
  const listed = { ...document, tags: ["inventory", "ops"], draft_count: 0 };
  test("tag badges link to the tag filter; the Tag filter asks for ?tag=; no Inventory documents only", async () => {
    const calls = api({
      "GET /api/documentation/documents?": () => [
        200,
        { documents: [listed, { ...listed, id: "doc-2", title: "Runbook", tags: [] }], total: 2 },
      ],
      "GET /api/documentation/documents?tag=inventory": () => [200, { documents: [listed], total: 1 }],
      "GET /api/documentation/documents?tag=ops": () => [200, { documents: [listed], total: 1 }],
      "GET /api/documentation/drafts?new=true": () => [200, { drafts: [] }],
      "GET /api/documentation/resources": () => [200, resources],
    });
    const router = show("/documents");
    const row = (await screen.findByRole("link", { name: "web-1 overview" })).closest("tr")!;
    expect(within(row).getByRole("link", { name: "inventory" })).toHaveAttribute("href", "/documents?tag=inventory");
    expect(within(row).getByRole("link", { name: "ops" })).toHaveAttribute("href", "/documents?tag=ops");
    const other = screen.getByRole("link", { name: "Runbook" }).closest("tr")!;
    expect(within(other).queryByRole("link", { name: "inventory" })).toBeNull();
    expect(screen.queryByRole("checkbox", { name: "Inventory documents only" })).toBeNull();
    fireEvent.change(screen.getByLabelText("Tag"), { target: { value: "inventory" } });
    await waitFor(() =>
      expect(calls.some((c) => c.key === "GET /api/documentation/documents?tag=inventory")).toBe(true),
    );
    await waitFor(() => expect(screen.queryByRole("link", { name: "Runbook" })).toBeNull());
    fireEvent.click(
      within(screen.getByRole("link", { name: "web-1 overview" }).closest("tr")!).getByRole("link", { name: "ops" }),
    );
    await waitFor(() => expect(router.state.location.search).toBe("?tag=ops"));
    await waitFor(() => expect(calls.some((c) => c.key === "GET /api/documentation/documents?tag=ops")).toBe(true));
    expect(screen.getByLabelText("Tag")).toHaveValue("ops");
  });

  test("the Drafts tab filters by tag too, and the tag carries over between the tabs", async () => {
    const calls = api({
      "GET /api/documentation/documents?tag=inventory": () => [200, { documents: [], total: 0 }],
      "GET /api/documentation/drafts?new=true": () => [200, { drafts: [] }],
      "GET /api/documentation/drafts?tag=inventory": () => [200, { drafts: [] }],
      "GET /api/documentation/resources": () => [200, resources],
    });
    const router = show("/documents?tag=inventory");
    expect(await screen.findByLabelText("Tag")).toHaveValue("inventory");
    fireEvent.mouseDown(screen.getByRole("tab", { name: "Drafts" }), { button: 0, ctrlKey: false });
    await waitFor(() => expect(calls.some((c) => c.key === "GET /api/documentation/drafts?tag=inventory")).toBe(true));
    expect(new URLSearchParams(router.state.location.search).get("tag")).toBe("inventory");
    expect(screen.getByLabelText("Tag")).toHaveValue("inventory");
  });
});
