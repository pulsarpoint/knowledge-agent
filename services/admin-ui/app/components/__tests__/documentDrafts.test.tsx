import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import userEvent from "@testing-library/user-event";
import { Toaster } from "~/components/ui/sonner";
import { rowAction, rowButtons } from "~/lib/__tests__/rowMenu";
import routes from "~/routes";
import { documentsRoute } from "~/lib/__tests__/pageRoutes";
import DraftPageView from "~/features/documents/DraftPageView";
import { expectOneToolbarRow } from "~/lib/__tests__/toolbarRow";

beforeEach(() => {
  render(<Toaster />);
});
vi.mock("../Markdown", () => ({ default: ({ content }: { content: string }) => <article>{content}</article> }));
const NativeRequest = globalThis.Request;
// React Router's Node Request cannot consume jsdom's AbortSignal. Fetch is mocked in this suite.
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

const resources = { code_projects: [], services: [], servers: [], deployments: [], environments: [] };
const doc = {
  id: "doc-1",
  slug: "runbook",
  title: "Runbook",
  description: "How to run it",
  purpose: "runbook",
  audience: "both",
  tags: [],
  code_project_ids: [],
  service_ids: [],
  server_ids: [],
  deployment_ids: [],
  revision: 1,
  version: 1,
  content_hash: "a",
  created_at: "2026-10-12T10:00:00Z",
  updated_at: "2026-10-12T10:00:00Z",
  draft_count: 2,
};
const newDraft = {
  id: "d9",
  document_id: null,
  slug: "disk-layout",
  title: "Disk layout",
  origin: "agent",
  session_id: "s1",
  session_title: "Disk layout",
  author: null,
  edited: false,
  revision: 1,
  base_version: null,
  live_version: null,
  stale: false,
  tags: [],
  created_at: "2026-10-12T10:00:00Z",
  updated_at: "2026-10-12T11:00:00Z",
};
const detail = {
  ...newDraft,
  description: "",
  purpose: "explanation",
  audience: "both",
  tags: [],
  markdown: "# Disks",
  resource_ids: {},
  sources: [
    {
      kind: "read",
      source_document_id: null,
      source_title: "Old overview",
      source_version: 4,
      deleted: true,
    },
  ],
  live_markdown: null,
  document_title: null,
};

/** A draft of a live document (doc-3, "Overview"): the draft page shows it (a brand-new draft opens at /documents/{id}). */
const ofLive = {
  ...detail,
  id: "d3",
  document_id: "doc-3",
  slug: "overview",
  title: "Overview",
  base_version: 1,
  live_version: 1,
  live_markdown: "# Old overview",
  markdown: "# Overview v2",
  document_title: "Overview",
};
const drafts3: Record<string, () => [number, unknown]> = {
  "GET /api/documentation/documents/doc-3/drafts": () => [200, { drafts: [ofLive] }],
};

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
      documentsRoute,
      { path: "/documents/drafts/:draftId", element: <DraftPageView /> },
      { path: "/documents/:documentId", element: <p>Document page</p> },
    ],
    { initialEntries: [url] },
  );
  render(<RouterProvider router={router} />);
  return router;
}

test("the list shows each document's drafts, a Has drafts filter and the new-document drafts", async () => {
  const calls = api({
    "GET /api/documentation/documents?": () => [200, { documents: [doc], total: 1 }],
    "GET /api/documentation/documents?has_drafts=true": () => [200, { documents: [doc], total: 1 }],
    "GET /api/documentation/resources": () => [200, resources],
    "GET /api/documentation/drafts?new=true": () => [200, { drafts: [newDraft] }],
    "POST /api/documentation/drafts/d9/discard": () => [200, {}],
  });
  show("/documents");
  expect(await screen.findByRole("link", { name: "2 drafts" })).toHaveAttribute("href", "/documents/doc-1/drafts");
  const section = within(screen.getByRole("region", { name: "New drafts" }));
  expect(section.getByRole("link", { name: "Disk layout" })).toHaveAttribute("href", "/documents/d9");
  expect(section.getByRole("link", { name: "Agent run" })).toHaveAttribute("href", "/sessions/s1");
  fireEvent.click(screen.getByRole("button", { name: "Filters" }));
  fireEvent.click(
    within(await screen.findByRole("dialog", { name: "Filters" })).getByRole("checkbox", { name: "Has drafts" }),
  );
  await waitFor(() =>
    expect(calls.some((c) => c.key === "GET /api/documentation/documents?has_drafts=true")).toBe(true),
  );
  expect(await screen.findByRole("button", { name: "Filters, 1 active", hidden: true })).toBeInTheDocument();
  await userEvent.keyboard("{Escape}");
  expect(rowButtons(section.getByRole("link", { name: "Disk layout" }).closest("tr")!)).toEqual([
    "Actions for Disk layout",
  ]);
  await rowAction(userEvent, "Disk layout", "Discard", section);
  fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Discard draft" }));
  await waitFor(() =>
    expect(calls.find((c) => c.key === "POST /api/documentation/drafts/d9/discard")?.body).toEqual({ revision: 1 }),
  );
});

test("the draft page of a live document's draft shows its changes and sources; Publish opens the document with a toast", async () => {
  const calls = api({
    ...drafts3,
    "GET /api/documentation/drafts/d3": () => [200, ofLive],
    "POST /api/documentation/drafts/d3/promote": () => [
      200,
      { document_id: "doc-3", version: 2, revision: 3, deleted_drafts: 0 },
    ],
  });
  const router = show("/documents/drafts/d3");
  expect(await screen.findByRole("link", { name: "Overview" })).toHaveAttribute("href", "/documents/doc-3");
  expect(screen.getByText("# Overview v2", { selector: "ins" })).toBeInTheDocument();
  expect(screen.getByText("Old overview v4 (deleted)")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Publish" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/documents/doc-3"));
  expect(await screen.findByText("Draft published as version 2.")).toBeInTheDocument();
  expect(calls.find((c) => c.key === "POST /api/documentation/drafts/d3/promote")!.body).toEqual({
    revision: 1,
    confirm_stale: false,
  });
});

test("Edit on the draft page saves the draft with its revision", async () => {
  let saved = false;
  const calls = api({
    "GET /api/documentation/drafts/d3": () => [
      200,
      saved ? { ...ofLive, markdown: "# Overview v3", revision: 2 } : ofLive,
    ],
    "PUT /api/documentation/drafts/d3": () => {
      saved = true;
      return [200, { ...ofLive, markdown: "# Overview v3", revision: 2, edited: true }];
    },
  });
  show("/documents/drafts/d3");
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Markdown"), { target: { value: "# Overview v3" } });
  expect(screen.queryByRole("button", { name: "Save draft" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText("Draft saved.")).toBeInTheDocument();
  expect(calls.find((c) => c.key === "PUT /api/documentation/drafts/d3")!.body).toEqual({
    revision: 1,
    title: "Overview",
    description: "",
    purpose: "explanation",
    audience: "both",
    tags: [],
    markdown: "# Overview v3",
  });
});

test("a stale publish keeps the warning, reloads the draft and sends confirm_stale on Publish anyway", async () => {
  const STALE = "This draft was based on version 1; the live version is now 2. Publishing replaces version 2's text.";
  let reloaded = false;
  const stale = {
    ...detail,
    id: "d2",
    document_id: "doc-1",
    slug: null,
    base_version: 1,
    live_version: 1,
    live_markdown: "# v1",
    stale: false,
  };
  const calls = api({
    "GET /api/documentation/documents/doc-1/drafts": () => [200, { drafts: [stale] }],
    "GET /api/documentation/drafts/d2": () => [
      200,
      reloaded ? { ...stale, live_version: 2, live_markdown: "# v2 live", stale: true } : stale,
    ],
    "POST /api/documentation/drafts/d2/promote": (body) => {
      if (body.confirm_stale) return [200, { document_id: "doc-1", version: 3, revision: 4, deleted_drafts: 0 }];
      reloaded = true;
      return [409, { error: { message: STALE, code: "STALE_BASE" } }];
    },
  });
  show("/documents/drafts/d2");
  fireEvent.click(await screen.findByRole("button", { name: "Publish" }));
  expect(await screen.findByText(STALE)).toBeInTheDocument();
  expect(await screen.findByText("stale")).toBeInTheDocument();
  expect(screen.getByText("# v2 live", { selector: "del" })).toBeInTheDocument();
  expect(screen.getByText(STALE)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Publish anyway" }));
  await waitFor(() =>
    expect(calls.filter((c) => c.key.endsWith("/promote")).map((c) => c.body)).toEqual([
      { revision: 1, confirm_stale: false },
      { revision: 1, confirm_stale: true },
    ]),
  );
});

test("a failed discard on the list shows why and refreshes the list so a retry works", async () => {
  let fails = true;
  const calls = api({
    "GET /api/documentation/documents?": () => [200, { documents: [doc], total: 1 }],
    "GET /api/documentation/resources": () => [200, resources],
    "GET /api/documentation/drafts?new=true": () => [
      200,
      { drafts: [fails ? newDraft : { ...newDraft, revision: 2 }] },
    ],
    "POST /api/documentation/drafts/d9/discard": (body) => {
      fails = false;
      return body.revision === 1 ? [409, { error: { message: "Changed meanwhile" } }] : [200, {}];
    },
  });
  show("/documents");
  const section = within(await screen.findByRole("region", { name: "New drafts" }));
  await rowAction(userEvent, "Disk layout", "Discard", section);
  fireEvent.click(await screen.findByRole("button", { name: "Discard draft" }));
  expect(await screen.findByText("Changed meanwhile")).toBeInTheDocument();
  await waitFor(() => expect(calls.filter((c) => c.key === "GET /api/documentation/drafts?new=true")).toHaveLength(2));
  expect(screen.getByText("Changed meanwhile")).toBeInTheDocument();
});

test("the Drafts sub-page lists the drafts with the resource filters: an environment asks GET /drafts?environment_id=", async () => {
  const ofDocument = {
    ...newDraft,
    id: "d4",
    document_id: "doc-1",
    slug: "runbook",
    title: "Runbook, shorter",
    origin: "manual",
    session_id: null,
    session_title: null,
    author: "ana@example.com",
    base_version: 1,
    live_version: 2,
    stale: true,
  };
  const calls = api({
    "GET /api/documentation/resources": () => [200, { ...resources, environments: [{ id: "env-1", name: "Prod" }] }],
    "GET /api/documentation/drafts": () => [200, { drafts: [ofDocument, newDraft] }],
    "GET /api/documentation/drafts?environment_id=env-1": () => [200, { drafts: [ofDocument] }],
  });
  const router = show("/documents/drafts");
  const list = within(await screen.findByRole("region", { name: "Drafts" }));
  expect(list.getByRole("link", { name: "Runbook, shorter" })).toHaveAttribute(
    "href",
    "/documents/doc-1/drafts?draft=d4",
  );
  expect(list.getByRole("link", { name: "Disk layout" })).toHaveAttribute("href", "/documents/d9");
  expect(within(list.getByRole("table")).getByText("New document")).toBeInTheDocument();
  expect(list.getByText("ana@example.com")).toBeInTheDocument();
  expect(list.getByRole("columnheader", { name: "Created" })).toBeInTheDocument();
  expect(list.queryByRole("columnheader", { name: "Updated" })).toBeNull();
  expect(screen.queryByLabelText("Search documents")).toBeNull();
  await router.navigate("/documents/drafts?environment_id=env-1");
  await waitFor(() =>
    expect(calls.some((c) => c.key === "GET /api/documentation/drafts?environment_id=env-1")).toBe(true),
  );
  await waitFor(() =>
    expect(
      within(screen.getByRole("region", { name: "Drafts" })).queryByRole("link", { name: "Disk layout" }),
    ).toBeNull(),
  );
  expect(screen.getByRole("link", { name: "Runbook, shorter" })).toBeInTheDocument();
  expect(calls.some((c) => c.key.startsWith("GET /api/documentation/documents"))).toBe(false);
});

test("the documents list: Skeleton rows while loading, pages by the API's total, rows per page go into ?limit=", async () => {
  const user = userEvent.setup();
  const calls = api({
    "GET /api/documentation/documents?": () => [200, { documents: [doc], total: 120 }],
    "GET /api/documentation/documents?offset=50": () => [
      200,
      { documents: [{ ...doc, id: "doc-9", title: "Later" }], total: 120 },
    ],
    "GET /api/documentation/documents?limit=20": () => [200, { documents: [doc], total: 120 }],
    "GET /api/documentation/resources": () => [200, resources],
    "GET /api/documentation/drafts?new=true": () => [200, { drafts: [] }],
  });
  const router = show("/documents");
  expect(screen.getAllByTestId("skeleton-row").length).toBeGreaterThan(0);
  expect(await screen.findByText("Showing 1–50 of 120")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Next" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Next page" }));
  expect(await screen.findByRole("link", { name: "Later" })).toBeInTheDocument();
  expect(router.state.location.search).toBe("?offset=50");
  await user.click(screen.getByRole("combobox", { name: "Rows per page" }));
  await user.click(screen.getByRole("option", { name: "20" }));
  await waitFor(() => expect(calls.some((c) => c.key === "GET /api/documentation/documents?limit=20")).toBe(true));
  expect(router.state.location.search).toBe("?limit=20");
  expect(await screen.findByText("Page 1 of 6")).toBeInTheDocument();
});

test("no documents at all shows an empty state with New document", async () => {
  api({
    "GET /api/documentation/documents?": () => [200, { documents: [], total: 0 }],
    "GET /api/documentation/resources": () => [200, resources],
    "GET /api/documentation/drafts?new=true": () => [200, { drafts: [] }],
  });
  show("/documents");
  expect(await screen.findByText("No documents yet")).toBeInTheDocument();
  expect(screen.getAllByRole("link", { name: "New document" })).toHaveLength(2);
});

test("routes: Documents is a parent route with Documents (the index) and Drafts as sub-pages", () => {
  const page = routes.find((item) => item.path === "documents")!;
  expect(page.file).toBe("./routes/documents.tsx");
  expect(page.children?.map((item) => [item.index ?? false, item.path, item.file])).toEqual([
    [true, undefined, "./routes/documentsIndex.tsx"],
    [false, "drafts", "./routes/documentsDrafts.tsx"],
  ]);
});

test.each([
  ["/documents?view=drafts", "/documents/drafts", ""],
  ["/documents?view=drafts&environment_id=env-1", "/documents/drafts", "?environment_id=env-1"],
  ["/documents?view=documents&tag=ops", "/documents", "?tag=ops"],
])("an old link %s opens its sub-page, keeping the filters", async (url, pathname, search) => {
  api({
    "GET /api/documentation/resources": () => [200, { ...resources, environments: [{ id: "env-1", name: "Prod" }] }],
    "GET /api/documentation/drafts": () => [200, { drafts: [] }],
    "GET /api/documentation/drafts?environment_id=env-1": () => [200, { drafts: [] }],
    "GET /api/documentation/documents?tag=ops": () => [200, { documents: [], total: 0 }],
    "GET /api/documentation/drafts?new=true": () => [200, { drafts: [] }],
  });
  const router = show(url);
  await waitFor(() => expect(router.state.location.pathname).toBe(pathname));
  expect(router.state.location.search).toBe(search);
  expect(await screen.findByText(pathname.endsWith("drafts") ? /No drafts/ : /No documents/)).toBeInTheDocument();
});

test("the section links carry the resource and tag filters, each sub-page with its help line", async () => {
  api({
    "GET /api/documentation/resources": () => [200, resources],
    "GET /api/documentation/documents?tag=ops&q=disk&has_drafts=true": () => [200, { documents: [doc], total: 1 }],
    "GET /api/documentation/drafts?new=true": () => [200, { drafts: [] }],
    "GET /api/documentation/drafts?tag=ops": () => [200, { drafts: [] }],
  });
  const router = show("/documents?tag=ops&q=disk&has_drafts=true");
  const nav = within(screen.getByRole("navigation", { name: "Documents sections" }));
  expect(nav.getByRole("link", { name: "Documents" })).toHaveAttribute("aria-current", "page");
  expect(nav.getByRole("link", { name: "Drafts" })).toHaveAttribute("href", "/documents/drafts?tag=ops");
  expect(screen.getByText("The live documents, with their links, versions and drafts.")).toBeVisible();
  expect(await screen.findByRole("link", { name: "New document" })).toHaveAttribute("href", "/documents/new");
  fireEvent.click(nav.getByRole("link", { name: "Drafts" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/documents/drafts"));
  expect(await screen.findByText("No drafts match these filters.")).toBeInTheDocument();
  expect(
    screen.getByText("Every draft, of new documents and of live ones, waiting to be published or discarded."),
  ).toBeVisible();
  expect(nav.getByRole("link", { name: "Documents" })).toHaveAttribute("href", "/documents?tag=ops");
});

test("documents: search, tag, code project, service, Filters and New document share one toolbar row", async () => {
  api({
    "GET /api/documentation/documents?": () => [200, { documents: [doc], total: 1 }],
    "GET /api/documentation/resources": () => [200, resources],
    "GET /api/documentation/drafts?new=true": () => [200, { drafts: [] }],
  });
  show("/documents");
  expectOneToolbarRow(
    await screen.findByRole("searchbox", { name: "Search documents" }),
    screen.getByRole("textbox", { name: "Tag" }),
    screen.getByRole("combobox", { name: "Code project" }),
    screen.getByRole("combobox", { name: "Service" }),
    screen.getByRole("button", { name: "Filters" }),
    screen.getByRole("link", { name: "New document" }),
  );
  expect(screen.queryByRole("combobox", { name: "Server" })).toBeNull();
  expect(screen.queryByRole("checkbox", { name: "Has drafts" })).toBeNull();
});

test("drafts: Server and Environment are behind Filters; they still filter, and Reset clears them", async () => {
  const calls = api({
    "GET /api/documentation/resources": () => [200, { ...resources, environments: [{ id: "env-1", name: "Prod" }] }],
    "GET /api/documentation/drafts?tag=ops": () => [200, { drafts: [] }],
    "GET /api/documentation/drafts?environment_id=env-1&tag=ops": () => [200, { drafts: [] }],
  });
  const router = show("/documents/drafts?tag=ops");
  const user = userEvent.setup();
  expectOneToolbarRow(
    await screen.findByRole("textbox", { name: "Tag" }),
    screen.getByRole("button", { name: "Filters" }),
    screen.getByRole("link", { name: "New document" }),
  );
  await waitFor(() => expect(calls.some((c) => c.key === "GET /api/documentation/drafts?tag=ops")).toBe(true));
  await user.click(screen.getByRole("button", { name: "Filters" }));
  const sheet = within(await screen.findByRole("dialog", { name: "Filters" }));
  expect(sheet.queryByRole("checkbox", { name: "Has drafts" })).toBeNull();
  await user.click(sheet.getByRole("combobox", { name: "Environment" }));
  await user.click(await screen.findByRole("option", { name: "Prod" }));
  await waitFor(() =>
    expect(calls.some((c) => c.key === "GET /api/documentation/drafts?environment_id=env-1&tag=ops")).toBe(true),
  );
  await user.click(sheet.getByRole("button", { name: "Reset" }));
  await waitFor(() => expect(router.state.location.search).toBe("?tag=ops"));
  await user.keyboard("{Escape}");
  expect(screen.getByRole("button", { name: "Filters" })).toBeInTheDocument();
});
