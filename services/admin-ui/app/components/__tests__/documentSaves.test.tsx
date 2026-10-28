import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import userEvent from "@testing-library/user-event";
import { Toaster } from "~/components/ui/sonner";
import { rowAction, rowButtons } from "~/lib/__tests__/rowMenu";
import DocumentEditorView from "~/features/documents/DocumentEditorView";
import DraftPageView from "~/features/documents/DraftPageView";
import { documentRoute, documentsRoute } from "~/lib/__tests__/pageRoutes";

beforeEach(() => {
  render(<Toaster />);
});
// Every save is a draft: one Save, the brand-new draft page, editing your draft of a live document, Save links.
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
const created = "2026-10-14T08:30:00Z";
const resources = {
  code_projects: [],
  services: [{ id: "svc-1", name: "Payments API" }],
  servers: [],
  deployments: [],
  environments: [{ id: "env-1", name: "Prod" }],
};
const noIds = { code_project_ids: [], service_ids: [], server_ids: [], deployment_ids: [], environment_ids: [] };
const live = {
  id: "doc-1",
  slug: "runbook",
  title: "Runbook",
  description: "How to run it",
  purpose: "runbook",
  audience: "both",
  tags: [],
  ...noIds,
  environment_ids: ["env-1"],
  revision: 2,
  version: 2,
  content_hash: "a",
  origin: "manual",
  from_draft: false,
  is_draft: false,
  published: true,
  created_at: at,
  updated_at: at,
  markdown: "# Runbook v2",
  source_snapshot: { ...resources, services: [] },
  versions: [{ version: 2, title: "Runbook", origin: "manual", created_at: at, from_draft: false, live: true }],
  drafts: [],
  resource_links: [
    {
      type: "environment",
      id: "env-1",
      name: "Prod",
      connection: "built_for",
      added_how: "person",
      added_by: "ana@example.com",
      session_id: null,
      created_at: at,
    },
  ],
};
/** A brand-new draft as GET /documents/{id} answers it. */
const brandNew = {
  id: "d5",
  slug: "new-runbook",
  title: "New runbook",
  description: "",
  purpose: "explanation",
  audience: "both",
  tags: [],
  markdown: "# How to",
  revision: 1,
  is_draft: true,
  published: false,
  origin: "manual",
  session_id: null,
  ...noIds,
  resource_links: [],
  versions: [],
  drafts: [],
  created_at: created,
  updated_at: created,
};
/** A draft as the drafts API answers it. */
const draftDetail = {
  id: "d7",
  document_id: "doc-1",
  slug: "runbook",
  title: "Runbook",
  origin: "manual",
  session_id: null,
  session_title: null,
  author: "ana@example.com",
  edited: false,
  revision: 1,
  base_version: 2,
  live_version: 2,
  stale: false,
  published: true,
  document_title: "Runbook",
  created_at: created,
  updated_at: created,
  description: "How to run it",
  purpose: "runbook",
  audience: "both",
  tags: [],
  markdown: "# Runbook v3",
  resource_ids: {},
  live_markdown: "# Runbook v2",
  sources: [],
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
      { path: "/documents/new", element: <DocumentEditorView /> },
      { path: "/documents/drafts/:draftId", element: <DraftPageView /> },
      documentRoute,
    ],
    { initialEntries: [url] },
  );
  render(<RouterProvider router={router} />);
  return router;
}
/** The document page's section links, by text. */
const sections = () =>
  within(screen.getByRole("navigation", { name: "Document sections" }))
    .getAllByRole("link")
    .map((link) => link.textContent);
const BANNER = "Draft — not published yet";

test("New → Save creates a brand-new draft and opens it at /documents/{id}; no Create document anywhere", async () => {
  const calls = api({
    "GET /api/documentation/resources": () => [200, resources],
    "POST /api/documentation/drafts": () => [201, { ...draftDetail, id: "d5", document_id: null, slug: "new-runbook" }],
    "GET /api/documentation/documents/d5": () => [200, brandNew],
  });
  const router = show("/documents/new");
  fireEvent.change(await screen.findByLabelText("Title"), { target: { value: "New runbook" } });
  fireEvent.change(screen.getByLabelText("Markdown"), { target: { value: "# How to" } });
  expect(screen.queryByRole("button", { name: "Create document" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Save new version" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Save draft" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/documents/d5"));
  expect(calls.find((c) => c.key === "POST /api/documentation/drafts")!.body).toEqual({
    slug: "new-runbook",
    title: "New runbook",
    description: "",
    purpose: "explanation",
    audience: "both",
    tags: [],
    markdown: "# How to",
    resource_ids: noIds,
  });
  expect(await screen.findByText(BANNER)).toBeInTheDocument();
  expect(calls.some((c) => c.key.startsWith("POST /api/documentation/documents"))).toBe(false);
});

test("the brand-new draft page: banner, Save/Publish/Discard, an editable name, no Version history or Drafts sections", async () => {
  const calls = api({
    "GET /api/documentation/documents/d5": () => [200, brandNew],
    "GET /api/documentation/resources": () => [200, resources],
    "PUT /api/documentation/drafts/d5": () => [200, { ...draftDetail, id: "d5", document_id: null, revision: 2 }],
  });
  show("/documents/d5");
  expect(await screen.findByText(BANNER)).toBeInTheDocument();
  for (const name of ["Save", "Publish", "Discard"]) expect(screen.getByRole("button", { name })).toBeInTheDocument();
  for (const name of ["Save new version", "Create document", "Delete", "Ask agent"])
    expect(screen.queryByRole("button", { name })).toBeNull();
  expect(sections()).toEqual(["Edit", "Agent runs"]);
  expect(screen.getByRole("tab", { name: "Linked resources" })).toBeInTheDocument();
  expect(screen.queryByText(/^Version \d/)).toBeNull();
  fireEvent.change(screen.getByLabelText("Slug"), { target: { value: "disk-runbook" } });
  expect(screen.queryByLabelText("Folder")).toBeNull(); // documents have no folders
  fireEvent.change(screen.getByLabelText("Markdown"), { target: { value: "# How to, edited" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(calls.find((c) => c.key === "PUT /api/documentation/drafts/d5")?.body).toEqual({
      revision: 1,
      slug: "disk-runbook",
      title: "New runbook",
      description: "",
      purpose: "explanation",
      audience: "both",
      tags: [],
      markdown: "# How to, edited",
    }),
  );
  expect(await screen.findByText("Draft saved.")).toBeInTheDocument();
});

test("Publish publishes the brand-new draft and the page shows the live document", async () => {
  let published = false;
  const calls = api({
    "GET /api/documentation/documents/d5": () => [
      200,
      published ? { ...live, ...noIds, id: "d5", title: "New runbook", version: 1, resource_links: [] } : brandNew,
    ],
    "GET /api/documentation/resources": () => [200, resources],
    "POST /api/documentation/drafts/d5/promote": () => {
      published = true;
      return [200, { document_id: "d5", version: 1, revision: 2, deleted_drafts: 0 }];
    },
  });
  show("/documents/d5");
  fireEvent.click(await screen.findByRole("button", { name: "Publish" }));
  await waitFor(() =>
    expect(calls.find((c) => c.key === "POST /api/documentation/drafts/d5/promote")?.body).toEqual({
      revision: 1,
      confirm_stale: false,
    }),
  );
  expect(await screen.findByText("Version 1")).toBeInTheDocument();
  expect(screen.queryByText(BANNER)).toBeNull();
  expect(sections()).toContain("Version history");
  expect(screen.queryByRole("button", { name: "Publish" })).toBeNull();
});

test("a Publish refused because a document took the name meanwhile opens the draft page with why", async () => {
  const TAKEN = "A document named new-runbook was created meanwhile. The draft is now a draft of it. Review it again.";
  const converted = { ...draftDetail, id: "d5", document_id: "doc-9", document_title: "New runbook" };
  api({
    "GET /api/documentation/documents/d5": () => [200, brandNew],
    "GET /api/documentation/resources": () => [200, resources],
    "POST /api/documentation/drafts/d5/promote": () => [409, { error: { message: TAKEN } }],
    "GET /api/documentation/drafts/d5": () => [200, converted],
  });
  const router = show("/documents/d5");
  fireEvent.click(await screen.findByRole("button", { name: "Publish" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/documents/drafts/d5"));
  // the draft page has loaded the draft (its document's link), and says why it opened
  expect(await screen.findByRole("link", { name: "New runbook" }, { timeout: 5000 })).toHaveAttribute(
    "href",
    "/documents/doc-9",
  );
  expect(screen.getByText(TAKEN)).toBeInTheDocument();
});

test("Discard on the brand-new draft page deletes it and goes back to Documents", async () => {
  const calls = api({
    "GET /api/documentation/documents/d5": () => [200, brandNew],
    "GET /api/documentation/resources": () => [200, resources],
    "POST /api/documentation/drafts/d5/discard": () => [200, {}],
  });
  const router = show("/documents/d5");
  fireEvent.click(await screen.findByRole("button", { name: "Discard" }));
  fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Discard draft" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/documents"));
  expect(calls.find((c) => c.key === "POST /api/documentation/drafts/d5/discard")?.body).toEqual({ revision: 1 });
});

test("on a live document the first Save creates your draft; the second Save updates that same draft", async () => {
  const calls = api({
    "GET /api/documentation/documents/doc-1": () => [200, live],
    "GET /api/documentation/resources": () => [200, resources],
    "POST /api/documentation/drafts": () => [201, draftDetail],
    "PUT /api/documentation/drafts/d7": (body) => [200, { ...draftDetail, revision: 2, markdown: body.markdown }],
  });
  show("/documents/doc-1");
  expect(screen.queryByRole("button", { name: "Save new version" })).toBeNull();
  fireEvent.change(await screen.findByLabelText("Markdown"), { target: { value: "# Runbook v3" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  const banner = `Editing your draft (created ${new Date(created).toLocaleString()}, from version 2). Save updates it; Publish makes it live.`;
  expect(await screen.findByText(banner)).toBeInTheDocument();
  expect(calls.find((c) => c.key === "POST /api/documentation/drafts")!.body).toEqual({
    document_id: "doc-1",
    base_version: 2,
    title: "Runbook",
    description: "How to run it",
    purpose: "runbook",
    audience: "both",
    tags: [],
    markdown: "# Runbook v3",
  });
  expect(screen.getByLabelText("Markdown")).toHaveValue("# Runbook v3");
  expect(sections()).toContain("Drafts (1)");
  fireEvent.change(screen.getByLabelText("Markdown"), { target: { value: "# Runbook v3, again" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(calls.find((c) => c.key === "PUT /api/documentation/drafts/d7")?.body).toMatchObject({
      revision: 1,
      markdown: "# Runbook v3, again",
    }),
  );
  fireEvent.change(screen.getByLabelText("Markdown"), { target: { value: "# Runbook v3, third" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(calls.filter((c) => c.key === "PUT /api/documentation/drafts/d7").map((c) => c.body.revision)).toEqual([
      1, 2,
    ]),
  );
  expect(calls.filter((c) => c.key === "POST /api/documentation/drafts")).toHaveLength(1);
  expect(calls.some((c) => c.key === "PUT /api/documentation/documents/doc-1")).toBe(false);
});

test("the Drafts section link carries the number of drafts", async () => {
  const row = {
    id: "x",
    title: "Runbook",
    origin: "manual",
    session_id: null,
    author: null,
    edited: false,
    base_version: 2,
    stale: false,
    published: true,
    created_at: at,
    updated_at: at,
  };
  api({
    "GET /api/documentation/documents/doc-1": () => [
      200,
      { ...live, drafts: [row, { ...row, id: "y" }, { ...row, id: "z" }] },
    ],
    "GET /api/documentation/resources": () => [200, resources],
  });
  show("/documents/doc-1");
  await screen.findByRole("navigation", { name: "Document sections" });
  expect(sections()).toContain("Drafts (3)");
});

test("Save links writes the links through the links route and keeps the new revision; a 409 shows its message", async () => {
  const STALE = "This document was changed by someone else. Reload it and try again.";
  let saves = 0;
  const calls = api({
    "GET /api/documentation/documents/doc-1": () => [200, live],
    "GET /api/documentation/resources": () => [200, resources],
    "GET /api/documentation/documents/doc-1/resource-history": () => [200, { history: [] }],
    "PUT /api/documentation/documents/doc-1/links": (body) =>
      saves++ === 0
        ? [200, { id: "doc-1", revision: 3, resource_links: live.resource_links }]
        : [409, { error: { message: STALE } }],
  });
  show("/documents/doc-1?tab=resources");
  const panel = within(await screen.findByRole("tabpanel", { name: "Linked resources" }));
  const save = panel.getByRole("button", { name: "Save links" });
  expect(save).toBeDisabled();
  fireEvent.click(panel.getByRole("checkbox", { name: "Payments API" }));
  fireEvent.click(save);
  await waitFor(() =>
    expect(calls.find((c) => c.key === "PUT /api/documentation/documents/doc-1/links")?.body).toEqual({
      revision: 2,
      ...noIds,
      service_ids: ["svc-1"],
      environment_ids: ["env-1"],
    }),
  );
  await waitFor(() => expect(save).toBeDisabled());
  fireEvent.click(panel.getByRole("checkbox", { name: "Payments API" }));
  fireEvent.click(save);
  expect(await panel.findByText(STALE)).toBeInTheDocument();
  expect(
    calls.filter((c) => c.key === "PUT /api/documentation/documents/doc-1/links").map((c) => c.body.revision),
  ).toEqual([2, 3]);
  expect(calls.some((c) => c.key === "POST /api/documentation/drafts")).toBe(false);
});

test("Save links on a brand-new draft moves the revision the next Save sends", async () => {
  const calls = api({
    "GET /api/documentation/documents/d5": () => [200, brandNew],
    "GET /api/documentation/resources": () => [200, resources],
    "GET /api/documentation/documents/d5/resource-history": () => [200, { history: [] }],
    "PUT /api/documentation/documents/d5/links": () => [200, { id: "d5", revision: 2, resource_links: [] }],
    "PUT /api/documentation/drafts/d5": () => [200, { ...draftDetail, id: "d5", document_id: null, revision: 3 }],
  });
  show("/documents/d5?tab=resources");
  const panel = within(await screen.findByRole("tabpanel", { name: "Linked resources" }));
  fireEvent.click(panel.getByRole("checkbox", { name: "Payments API" }));
  fireEvent.click(panel.getByRole("button", { name: "Save links" }));
  await waitFor(() =>
    expect(calls.find((c) => c.key === "PUT /api/documentation/documents/d5/links")?.body.revision).toBe(1),
  );
  fireEvent.click(screen.getByRole("tab", { name: "Write" }));
  fireEvent.change(screen.getByLabelText("Markdown"), { target: { value: "# How to, edited" } });
  await waitFor(() => expect(screen.getByRole("button", { name: "Save" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(calls.find((c) => c.key === "PUT /api/documentation/drafts/d5")?.body.revision).toBe(2));
});

test("opening a draft of a live document at /documents/{id} goes to its draft page", async () => {
  api({
    "GET /api/documentation/documents/d7": () => [404, { error: { message: "Document not found" } }],
    "GET /api/documentation/resources": () => [200, resources],
    "GET /api/documentation/drafts/d7": () => [200, draftDetail],
  });
  const router = show("/documents/d7");
  await waitFor(() => expect(router.state.location.pathname).toBe("/documents/drafts/d7"));
  expect(await screen.findByRole("region", { name: "Draft" })).toBeInTheDocument();
});

test("the draft page of a brand-new draft redirects to /documents/{id}", async () => {
  api({
    "GET /api/documentation/drafts/d5": () => [200, { ...draftDetail, id: "d5", document_id: null, published: false }],
    "GET /api/documentation/documents/d5": () => [200, brandNew],
    "GET /api/documentation/resources": () => [200, resources],
  });
  const router = show("/documents/drafts/d5");
  await waitFor(() => expect(router.state.location.pathname).toBe("/documents/d5"));
  expect(await screen.findByText(BANNER)).toBeInTheDocument();
});

test("Documents → Drafts: a brand-new draft is Not published with Open, Publish, Discard; a draft of a document shows its title", async () => {
  const fresh = {
    ...draftDetail,
    id: "d5",
    document_id: null,
    slug: "new-runbook",
    title: "New runbook",
    published: false,
    document_title: null,
  };
  const calls = api({
    "GET /api/documentation/drafts": () => [200, { drafts: [fresh, { ...draftDetail, title: "Runbook rewrite" }] }],
    "GET /api/documentation/resources": () => [200, resources],
    "POST /api/documentation/drafts/d5/promote": () => [
      200,
      { document_id: "d5", version: 1, revision: 2, deleted_drafts: 0 },
    ],
  });
  show("/documents/drafts");
  const table = within(await screen.findByRole("region", { name: "Drafts" }));
  const rows = table.getAllByRole("row").slice(1);
  const brandNewRow = within(rows[0]);
  expect(brandNewRow.getByText("Not published")).toBeInTheDocument();
  expect(brandNewRow.getByRole("link", { name: "New runbook" })).toHaveAttribute("href", "/documents/d5");
  // Publish inline, Discard in the menu; no Open beside the title link
  expect(rowButtons(rows[0])).toEqual(["Publish New runbook", "Actions for New runbook"]);
  await rowAction(userEvent, "New runbook", "Discard", brandNewRow);
  expect(await screen.findByRole("alertdialog")).toHaveTextContent("Discard “New runbook”?");
  fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Cancel" }));
  const ofLive = within(rows[1]);
  expect(ofLive.queryByText("Not published")).toBeNull();
  expect(ofLive.getByRole("link", { name: "Runbook" })).toHaveAttribute("href", "/documents/doc-1");
  expect(ofLive.getByRole("link", { name: "Runbook rewrite" })).toHaveAttribute(
    "href",
    "/documents/doc-1/drafts?draft=d7",
  );
  expect(rowButtons(rows[1])).toEqual(["Actions for Runbook rewrite"]);
  fireEvent.click(brandNewRow.getByRole("button", { name: "Publish New runbook" }));
  await waitFor(() =>
    expect(calls.find((c) => c.key === "POST /api/documentation/drafts/d5/promote")?.body).toEqual({
      revision: 1,
      confirm_stale: false,
    }),
  );
});

test("New → Save with a name a document already uses shows the refusal under the name field", async () => {
  const IN_USE = "A document or draft named runbook already exists. Choose another name.";
  api({
    "GET /api/documentation/resources": () => [200, resources],
    "POST /api/documentation/drafts": () => [409, { error: { message: IN_USE } }],
  });
  const router = show("/documents/new");
  fireEvent.change(await screen.findByLabelText("Title"), { target: { value: "Runbook" } });
  fireEvent.change(screen.getByLabelText("Markdown"), { target: { value: "# Another runbook" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  const slug = screen.getByLabelText("Slug");
  const field = within(slug.closest<HTMLElement>('[data-slot="field"]')!);
  expect(await field.findByText(IN_USE)).toBeInTheDocument();
  expect(screen.getAllByText(IN_USE)).toHaveLength(1);
  expect(slug).toHaveAttribute("aria-invalid", "true");
  expect(router.state.location.pathname).toBe("/documents/new");
  fireEvent.change(slug, { target: { value: "runbook-2" } });
  expect(screen.queryByText(IN_USE)).toBeNull();
});
