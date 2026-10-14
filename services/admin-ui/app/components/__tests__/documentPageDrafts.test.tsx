import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import DocumentEditorView from "~/features/documents/DocumentEditorView";

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
const resources = { code_projects: [], services: [], servers: [], deployments: [], environments: [] };
const document = {
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
  environment_ids: [],
  revision: 2,
  version: 2,
  content_hash: "a",
  origin: "manual",
  from_draft: true,
  created_at: at,
  updated_at: at,
  markdown: "# Runbook v2 live",
  source_snapshot: resources,
  versions: [
    { version: 2, title: "Runbook", origin: "manual", created_at: at, from_draft: true, live: true },
    { version: 1, title: "Runbook", origin: "manual", created_at: at, from_draft: false, live: false },
  ],
  drafts: [],
  resource_links: [],
};
const summary = {
  id: "d1",
  document_id: "doc-1",
  slug: null,
  folder: null,
  title: "Runbook",
  origin: "agent",
  session_id: "s1",
  session_title: "Runbook",
  author: null,
  edited: false,
  revision: 1,
  base_version: 1,
  live_version: 2,
  stale: true,
  created_at: at,
  updated_at: at,
};
const detail = {
  ...summary,
  description: "How to run it",
  purpose: "runbook",
  audience: "both",
  tags: [],
  markdown: "# Runbook draft text",
  resource_ids: {},
  live_markdown: "# Runbook v2 live",
  document_title: "Runbook",
  sources: [
    {
      kind: "parent",
      source_document_id: "doc-1",
      source_title: "Runbook",
      source_version: 1,
      deleted: false,
    },
  ],
};
const STALE = "This draft was based on version 1; the live version is now 2. Promoting replaces version 2's text.";
const base: Record<string, (body: any) => [number, unknown]> = {
  "GET /api/documentation/documents/doc-1": () => [200, document],
  "GET /api/documentation/resources": () => [200, resources],
  "GET /api/documentation/documents/doc-1/drafts": () => [200, { drafts: [summary] }],
  "GET /api/documentation/drafts/d1": () => [200, detail],
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
      { path: "/documents/new", element: <DocumentEditorView /> },
      { path: "/documents/drafts/:draftId", element: <p>Draft page</p> },
      { path: "/documents/:documentId", element: <DocumentEditorView /> },
    ],
    { initialEntries: [url] },
  );
  render(<RouterProvider router={router} />);
  return router;
}

test("Save keeps the editor text as the person's draft of the document and the editor goes on editing it", async () => {
  const calls = api({ ...base, "POST /api/documentation/drafts": () => [201, { ...detail, stale: false }] });
  show("/documents/doc-1");
  fireEvent.change(await screen.findByLabelText("Markdown"), { target: { value: "# Runbook v3 draft" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(calls.find((c) => c.key === "POST /api/documentation/drafts")?.body).toEqual({
      document_id: "doc-1",
      title: "Runbook",
      description: "How to run it",
      purpose: "runbook",
      audience: "both",
      tags: [],
      markdown: "# Runbook v3 draft",
    }),
  );
  expect(await screen.findByText("Draft saved.")).toBeInTheDocument();
  expect(screen.getByText(/^Editing your draft \(created .*, from version 1\)\./)).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Write", selected: true })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Drafts 1" })).toBeInTheDocument();
  expect(screen.getByLabelText("Markdown")).toHaveValue("# Runbook v3 draft");
});

test("Save on a new document creates a brand-new draft and opens it at /documents/{id}", async () => {
  const calls = api({
    "GET /api/documentation/resources": () => [200, resources],
    "POST /api/documentation/drafts": () => [201, { ...detail, id: "d5", document_id: null, slug: "new-runbook" }],
    "GET /api/documentation/documents/d5": () => [
      200,
      { ...document, id: "d5", slug: "new-runbook", is_draft: true, published: false, versions: [] },
    ],
  });
  const router = show("/documents/new");
  fireEvent.change(await screen.findByLabelText("Title"), { target: { value: "New runbook" } });
  fireEvent.change(screen.getByLabelText("Markdown"), { target: { value: "# How to" } });
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
    resource_ids: { code_project_ids: [], service_ids: [], server_ids: [], deployment_ids: [], environment_ids: [] },
  });
});

test("a stale draft shows the diff against the live version and promotes only on Promote anyway", async () => {
  const promoted = { document_id: "doc-1", version: 3, revision: 4, deleted_drafts: 0, export_error: null };
  const calls = api({
    ...base,
    "POST /api/documentation/drafts/d1/promote": (body) =>
      body.confirm_stale ? [200, promoted] : [409, { error: { message: STALE, code: "STALE_BASE" } }],
  });
  show("/documents/doc-1?tab=drafts&draft=d1");
  const list = await screen.findByRole("list", { name: "Drafts" });
  expect(within(list).getByText("stale")).toBeInTheDocument();
  expect(within(list).getByText(/Agent run/)).toBeInTheDocument();
  const view = within(await screen.findByRole("region", { name: "Draft" }));
  expect(view.getByText("# Runbook draft text", { selector: "ins" })).toBeInTheDocument();
  expect(view.getByText("# Runbook v2 live", { selector: "del" })).toBeInTheDocument();
  expect(view.getByRole("link", { name: "Agent run" })).toHaveAttribute("href", "/sessions/s1");
  fireEvent.click(view.getByRole("button", { name: "Promote" }));
  expect(await view.findByText(STALE)).toBeInTheDocument();
  fireEvent.click(view.getByRole("button", { name: "Promote anyway" }));
  await waitFor(() =>
    expect(calls.filter((c) => c.key === "POST /api/documentation/drafts/d1/promote").map((c) => c.body)).toEqual([
      { revision: 1, confirm_stale: false },
      { revision: 1, confirm_stale: true },
    ]),
  );
  expect(await screen.findByText("Draft promoted to version 3.")).toBeInTheDocument();
});

test("Edit loads a draft into the editor and Save then updates that draft", async () => {
  const calls = api({
    ...base,
    "PUT /api/documentation/drafts/d1": () => [200, { ...detail, revision: 2, edited: true }],
  });
  show("/documents/doc-1?tab=drafts&draft=d1");
  const view = within(await screen.findByRole("region", { name: "Draft" }));
  fireEvent.click(view.getByRole("button", { name: "Edit" }));
  expect(await screen.findByLabelText("Markdown")).toHaveValue("# Runbook draft text");
  expect(screen.getByText(/^Editing your draft \(created .*, from version 1\)/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Markdown"), { target: { value: "# Runbook draft text, edited" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(calls.find((c) => c.key === "PUT /api/documentation/drafts/d1")?.body).toMatchObject({
      revision: 1,
      markdown: "# Runbook draft text, edited",
    }),
  );
  expect(calls.some((c) => c.key === "POST /api/documentation/drafts")).toBe(false);
});

test("Connections adds and removes dependencies and shows what uses the document and what built it", async () => {
  const overview = { id: "doc-2", title: "Overview", slug: "overview", folder: null, version: 1 };
  const connections = {
    depends_on: [],
    used_by: [{ id: "doc-7", title: "Billing guide", slug: "billing-guide", folder: null, version: 3 }],
    built_from: [
      {
        kind: "read",
        source_document_id: null,
        source_title: "Old notes",
        source_version: 2,
        deleted: true,
      },
    ],
  };
  const calls = api({
    ...base,
    "GET /api/documentation/documents/doc-1/connections": () => [200, connections],
    "GET /api/documentation/documents?q=over": () => [
      200,
      { documents: [{ ...document, id: "doc-2", title: "Overview" }, document], total: 2 },
    ],
    "POST /api/documentation/documents/doc-1/links": () => [201, { ...connections, depends_on: [overview] }],
    "DELETE /api/documentation/documents/doc-1/links/doc-2": () => [200, connections],
  });
  show("/documents/doc-1?tab=connections");
  const panel = within(await screen.findByRole("region", { name: "Connections" }));
  expect(await panel.findByRole("link", { name: "Billing guide" })).toHaveAttribute("href", "/documents/doc-7");
  expect(panel.getByText("Old notes v2 (deleted)")).toBeInTheDocument();
  fireEvent.change(panel.getByLabelText("Find a document"), { target: { value: "over" } });
  const add = await panel.findByRole("button", { name: "Add Overview" });
  expect(panel.queryByRole("button", { name: "Add Runbook" })).toBeNull(); // never itself
  fireEvent.click(add);
  await waitFor(() =>
    expect(calls.find((c) => c.key === "POST /api/documentation/documents/doc-1/links")?.body).toEqual({
      to_document_id: "doc-2",
    }),
  );
  fireEvent.click(await panel.findByRole("button", { name: "Remove Overview" }));
  await waitFor(() =>
    expect(calls.some((c) => c.key === "DELETE /api/documentation/documents/doc-1/links/doc-2")).toBe(true),
  );
});

test("version history notes the versions promoted from a draft", async () => {
  api(base);
  show("/documents/doc-1?tab=history");
  expect(await screen.findByRole("button", { name: /Version 2 · Current · from draft/ })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /^Version 1/ })).not.toHaveTextContent("from draft");
});

test("a notice handed over by the draft page (a promote whose export failed) shows on the document page", async () => {
  api(base);
  const notice = "Draft promoted to version 1. Saved. Writing the document file failed; use Export to folder to retry.";
  const router = createMemoryRouter([{ path: "/documents/:documentId", element: <DocumentEditorView /> }], {
    initialEntries: [{ pathname: "/documents/doc-1", state: { notice } }],
  });
  render(<RouterProvider router={router} />);
  expect(await screen.findByText(notice)).toBeInTheDocument();
});

test("with unsaved changes, Edit on a draft asks first; Keep editing keeps the text, Discard changes loads the draft", async () => {
  api(base);
  show("/documents/doc-1?tab=drafts&draft=d1");
  const view = within(await screen.findByRole("region", { name: "Draft" }));
  fireEvent.change(screen.getByLabelText("Markdown"), { target: { value: "# My unsaved text" } });
  fireEvent.click(view.getByRole("button", { name: "Edit" }));
  const dialog = within(await screen.findByRole("alertdialog"));
  expect(dialog.getByText("Discard unsaved changes?")).toBeInTheDocument();
  fireEvent.click(dialog.getByRole("button", { name: "Keep editing" }));
  await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
  expect(screen.getByLabelText("Markdown")).toHaveValue("# My unsaved text");
  expect(screen.queryByText(/Editing your draft/)).toBeNull();
  fireEvent.click(view.getByRole("button", { name: "Edit" }));
  fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Discard changes" }));
  await waitFor(() => expect(screen.getByLabelText("Markdown")).toHaveValue("# Runbook draft text"));
  expect(screen.getByText(/Editing your draft/)).toBeInTheDocument();
});

test("with unsaved changes, Promote on the Drafts tab asks first; Keep editing promotes nothing", async () => {
  const promoted = { document_id: "doc-1", version: 3, revision: 4, deleted_drafts: 0, export_error: null };
  const calls = api({ ...base, "POST /api/documentation/drafts/d1/promote": () => [200, promoted] });
  show("/documents/doc-1?tab=drafts&draft=d1");
  const view = within(await screen.findByRole("region", { name: "Draft" }));
  fireEvent.change(screen.getByLabelText("Markdown"), { target: { value: "# My unsaved text" } });
  fireEvent.click(view.getByRole("button", { name: "Promote" }));
  fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Keep editing" }));
  await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
  expect(calls.some((c) => c.key.endsWith("/promote"))).toBe(false);
  expect(screen.getByLabelText("Markdown")).toHaveValue("# My unsaved text");
  fireEvent.click(view.getByRole("button", { name: "Promote" }));
  fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Discard changes" }));
  await waitFor(() =>
    expect(calls.filter((c) => c.key === "POST /api/documentation/drafts/d1/promote").map((c) => c.body)).toEqual([
      { revision: 1, confirm_stale: false },
    ]),
  );
  expect(await screen.findByText("Draft promoted to version 3.")).toBeInTheDocument();
});

test("without unsaved changes, Edit loads the draft at once", async () => {
  api(base);
  show("/documents/doc-1?tab=drafts&draft=d1");
  fireEvent.click(within(await screen.findByRole("region", { name: "Draft" })).getByRole("button", { name: "Edit" }));
  expect(await screen.findByLabelText("Markdown")).toHaveValue("# Runbook draft text");
  expect(screen.queryByRole("alertdialog")).toBeNull();
});

test("with unsaved changes, Ask agent opens the pane and keeps the text; only the first message asks", async () => {
  const OPTIONS = "GET /api/sessions/options?document_id=doc-1";
  const options = {
    models: [],
    default_model_id: null,
    prompts: [],
    default_prompt_id: null,
    resources: { servers: [], services: [], environments: [], code_projects: [], collections: [], documents: [] },
    tools: [],
    experts: [],
  };
  const calls = api({ ...base, [OPTIONS]: () => [200, options] });
  show("/documents/doc-1");
  fireEvent.change(await screen.findByLabelText("Markdown"), { target: { value: "# My unsaved text" } });
  expect(calls.some((c) => c.key === OPTIONS)).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Ask agent" }));
  expect(await screen.findByRole("region", { name: "Ask agent" })).toBeInTheDocument();
  expect(screen.queryByRole("alertdialog")).toBeNull();
  await waitFor(() => expect(calls.some((c) => c.key === OPTIONS)).toBe(true));
  expect(screen.getByLabelText("Markdown")).toHaveValue("# My unsaved text");
});

test("the Drafts tab lists drafts newest first; Promote with two other drafts asks first", async () => {
  const older = { ...summary, id: "d0", title: "First try", created_at: "2026-10-11T10:00:00Z", stale: false };
  const newer = { ...summary, id: "d2", title: "Third try", created_at: "2026-10-13T10:00:00Z", stale: false };
  const fresh = { ...detail, stale: false };
  const calls = api({
    ...base,
    "GET /api/documentation/documents/doc-1/drafts": () => [
      200,
      { drafts: [newer, { ...summary, stale: false }, older] },
    ],
    "GET /api/documentation/drafts/d1": () => [200, fresh],
    "POST /api/documentation/drafts/d1/promote": () => [
      200,
      { document_id: "doc-1", version: 3, revision: 4, deleted_drafts: 2, export_error: null },
    ],
  });
  show("/documents/doc-1?tab=drafts&draft=d1");
  const list = within(await screen.findByRole("list", { name: "Drafts" }));
  expect(list.getAllByRole("listitem").map((item) => within(item).getByRole("button").textContent)).toEqual([
    "Third try",
    "Runbook",
    "First try",
  ]);
  const view = within(await screen.findByRole("region", { name: "Draft" }));
  fireEvent.click(view.getByRole("button", { name: "Promote" }));
  const dialog = within(await screen.findByRole("alertdialog"));
  expect(dialog.getByText("Promote and delete 2 other draft(s)?")).toBeInTheDocument();
  expect(calls.some((c) => c.key.endsWith("/promote"))).toBe(false);
  fireEvent.click(dialog.getByRole("button", { name: "Promote" }));
  await waitFor(() =>
    expect(calls.filter((c) => c.key === "POST /api/documentation/drafts/d1/promote").map((c) => c.body)).toEqual([
      { revision: 1, confirm_stale: false },
    ]),
  );
  expect(await screen.findByText("Draft promoted to version 3.")).toBeInTheDocument();
});
