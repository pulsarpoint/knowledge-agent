import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import DocumentsView from "~/features/documents/DocumentsView";
import DraftPageView from "~/features/documents/DraftPageView";

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
  folder: "servers/web-1",
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
  folder: null,
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
      { path: "/documents", element: <DocumentsView /> },
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
  expect(await screen.findByRole("link", { name: "2 drafts" })).toHaveAttribute("href", "/documents/doc-1?tab=drafts");
  const section = within(screen.getByRole("region", { name: "New drafts" }));
  expect(section.getByRole("link", { name: "Disk layout" })).toHaveAttribute("href", "/documents/d9");
  expect(section.getByText("servers/web-1")).toBeInTheDocument();
  expect(section.getByRole("link", { name: "Agent run" })).toHaveAttribute("href", "/sessions/s1");
  fireEvent.click(screen.getByRole("checkbox", { name: "Has drafts" }));
  await waitFor(() =>
    expect(calls.some((c) => c.key === "GET /api/documentation/documents?has_drafts=true")).toBe(true),
  );
  fireEvent.click(section.getByRole("button", { name: "Discard Disk layout" }));
  fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Discard draft" }));
  await waitFor(() =>
    expect(calls.find((c) => c.key === "POST /api/documentation/drafts/d9/discard")?.body).toEqual({ revision: 1 }),
  );
});

test("the draft page of a live document's draft shows its changes and sources; Promote opens the document", async () => {
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
  fireEvent.click(screen.getByRole("button", { name: "Promote" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/documents/doc-3"));
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

test("a stale promote keeps the warning, reloads the draft and sends confirm_stale on Promote anyway", async () => {
  const STALE = "This draft was based on version 1; the live version is now 2. Promoting replaces version 2's text.";
  let reloaded = false;
  const stale = {
    ...detail,
    id: "d2",
    document_id: "doc-1",
    slug: null,
    folder: null,
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
  fireEvent.click(await screen.findByRole("button", { name: "Promote" }));
  expect(await screen.findByText(STALE)).toBeInTheDocument();
  expect(await screen.findByText("stale")).toBeInTheDocument();
  expect(screen.getByText("# v2 live", { selector: "del" })).toBeInTheDocument();
  expect(screen.getByText(STALE)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Promote anyway" }));
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
  fireEvent.click(section.getByRole("button", { name: /Discard/ }));
  fireEvent.click(await screen.findByRole("button", { name: "Discard draft" }));
  expect(await screen.findByText("Changed meanwhile")).toBeInTheDocument();
  await waitFor(() => expect(calls.filter((c) => c.key === "GET /api/documentation/drafts?new=true")).toHaveLength(2));
  expect(screen.getByText("Changed meanwhile")).toBeInTheDocument();
});

test("the Drafts tab lists the drafts with the resource filters: an environment asks GET /drafts?environment_id=", async () => {
  const ofDocument = {
    ...newDraft,
    id: "d4",
    document_id: "doc-1",
    slug: "runbook",
    folder: null,
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
  const router = show("/documents?view=drafts");
  const list = within(await screen.findByRole("region", { name: "Drafts" }));
  expect(list.getByRole("link", { name: "Runbook, shorter" })).toHaveAttribute(
    "href",
    "/documents/doc-1?tab=drafts&draft=d4",
  );
  expect(list.getByRole("link", { name: "Disk layout" })).toHaveAttribute("href", "/documents/d9");
  expect(list.getByText("New document in servers/web-1/")).toBeInTheDocument();
  expect(list.getByText("ana@example.com")).toBeInTheDocument();
  expect(list.getByRole("columnheader", { name: "Created" })).toBeInTheDocument();
  expect(list.queryByRole("columnheader", { name: "Updated" })).toBeNull();
  expect(screen.queryByLabelText("Search documents")).toBeNull();
  await router.navigate("/documents?view=drafts&environment_id=env-1");
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
