import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import routes from "~/routes";
import { Toaster } from "~/components/ui/sonner";
import { documentRoute } from "~/lib/__tests__/pageRoutes";

beforeEach(() => {
  render(<Toaster />);
});
vi.mock("../Markdown", () => ({ default: ({ content }: { content: string }) => <article>{content}</article> }));
vi.mock("~/features/sessions/AgentChat", () => ({
  default: (props: { initialRunId: string | null }) => (
    <section aria-label="Ask agent">{`run ${props.initialRunId ?? "none"}`}</section>
  ),
}));
const NativeRequest = globalThis.Request;
beforeEach(() => {
  vi.stubGlobal(
    "Request",
    class extends NativeRequest {
      constructor(input: RequestInfo | URL, init?: RequestInit) {
        super(input, { ...init, signal: undefined });
      }
    },
  );
  try {
    localStorage.clear();
  } catch {
    /* none */
  }
});
afterEach(() => vi.unstubAllGlobals());

const at = "2026-10-12T10:00:00Z";
const resources = { code_projects: [], services: [], servers: [], deployments: [], environments: [] };
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
  version: 2,
  content_hash: "a",
  origin: "manual",
  from_draft: false,
  created_at: at,
  updated_at: at,
  markdown: "# Runbook live",
  source_snapshot: resources,
  versions: [{ version: 2, title: "Runbook", origin: "manual", created_at: at, from_draft: false, live: true }],
  drafts: [{ id: "d1" }],
  resource_links: [],
};
const draft = { ...document, id: "d9", is_draft: true, drafts: [], versions: [] };
const responses: Record<string, unknown> = {
  "/api/documentation/documents/doc-1": document,
  "/api/documentation/documents/d9": draft,
  "/api/documentation/resources": resources,
  "/api/documentation/documents/doc-1/drafts": {
    drafts: [
      {
        id: "d1",
        document_id: "doc-1",
        title: "Runbook, shorter",
        origin: "manual",
        session_id: null,
        session_title: null,
        author: "ana@example.com",
        revision: 1,
        base_version: 2,
        live_version: 2,
        stale: false,
        created_at: at,
        updated_at: at,
      },
    ],
  },
  "/api/documentation/documents/doc-1/connections": {
    revision: 2,
    resources: [],
    depends_on: [],
    used_by: [],
    built_from: [],
  },
  "/api/documentation/documents/doc-1/resource-history": { history: [] },
  "/api/sessions?document_id=doc-1": { sessions: [] },
  "/api/sessions?document_id=d9": { sessions: [] },
};
beforeEach(() =>
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      // the Ask agent pane's settings are not part of these tests
      if (!(url in responses)) return new Response(JSON.stringify({ error: { message: "not here" } }), { status: 404 });
      return new Response(JSON.stringify(responses[url]));
    }),
  ),
);

function show(url: string) {
  const router = createMemoryRouter([documentRoute, { path: "/documents", element: <p>All documents list</p> }], {
    initialEntries: [url],
  });
  render(<RouterProvider router={router} />);
  return router;
}
const nav = () => within(screen.getByRole("navigation", { name: "Document sections" }));
const where = (router: ReturnType<typeof show>) => router.state.location.pathname + router.state.location.search;

test("routes: the document page has History, Drafts, Connections and Agent runs as sub-pages", () => {
  const page = routes.find((item) => item.path === "documents/:documentId")!;
  expect(page.file).toBe("./routes/documentDetail.tsx");
  expect(page.children?.map((item) => [item.index ?? false, item.path, item.file])).toEqual([
    [true, undefined, "./routes/documentWrite.tsx"],
    [false, "history", "./routes/documentHistory.tsx"],
    [false, "drafts", "./routes/documentDrafts.tsx"],
    [false, "connections", "./routes/documentConnections.tsx"],
    [false, "agent-runs", "./routes/documentAgentRuns.tsx"],
    [false, "agent/:runId", "./routes/documentAgentRun.tsx"],
  ]);
});

test.each([
  ["", "Edit", "The document's text. Save keeps your changes as your draft; publish a draft on Drafts."],
  ["/history", "Version history", "Every published version. Open one to read it or start a new draft from it."],
  [
    "/drafts",
    "Drafts (1)",
    "Drafts of this document by people and agents: compare one with the live version, edit it or publish it.",
  ],
  [
    "/connections",
    "Connections",
    "The resources this document is linked to, documents it depends on, documents that depend on it, and what it was built from.",
  ],
  ["/agent-runs", "Agent runs", "Ask agent runs on this document, open ones first, with the drafts they wrote."],
])("the sub-page %s is open in the section links with its help line", async (page, label, help) => {
  show(`/documents/doc-1${page}`);
  expect(await screen.findByText(help)).toBeVisible();
  expect(
    nav()
      .getAllByRole("link")
      .map((link) => link.textContent),
  ).toEqual(["Edit", "Version history", "Drafts (1)", "Connections", "Agent runs"]);
  expect(nav().getByRole("link", { name: label })).toHaveAttribute("aria-current", "page");
  // the editor's fields are on Edit only
  if (page) expect(screen.queryByLabelText("Markdown")).toBeNull();
  else expect(screen.getByLabelText("Markdown")).toBeInTheDocument();
});

test("a brand-new draft has Edit and Agent runs only", async () => {
  show("/documents/d9");
  expect(await screen.findByText("Draft — not published yet")).toBeInTheDocument();
  expect(
    nav()
      .getAllByRole("link")
      .map((link) => link.textContent),
  ).toEqual(["Edit", "Agent runs"]);
  expect(
    screen.getByText("The document's text and links. Save keeps it as a draft until it is published."),
  ).toBeVisible();
});

test("a brand-new draft's live-only sub-page opens Edit, keeping the query", async () => {
  const router = show("/documents/d9/history?draft=x");
  await waitFor(() => expect(where(router)).toBe("/documents/d9?draft=x"));
  expect(await screen.findByLabelText("Markdown")).toBeInTheDocument();
});

test.each([
  ["/documents/doc-1?tab=history", "/documents/doc-1/history"],
  ["/documents/doc-1?tab=drafts&draft=d1", "/documents/doc-1/drafts?draft=d1"],
  ["/documents/doc-1?tab=connections", "/documents/doc-1/connections"],
  ["/documents/doc-1?tab=agent-runs", "/documents/doc-1/agent-runs"],
  ["/documents/doc-1?tab=write", "/documents/doc-1"],
  ["/documents/doc-1/agent/r1", "/documents/doc-1?run=r1"],
  ["/documents/doc-1/agent/r1?tab=drafts&draft=d1", "/documents/doc-1/drafts?draft=d1&run=r1"],
])("an old link %s lands on the same content (%s)", async (url, target) => {
  const router = show(url);
  await waitFor(() => expect(where(router)).toBe(target));
  await screen.findByRole("navigation", { name: "Document sections" });
  if (target.includes("run=")) expect(await screen.findByText("run r1")).toBeInTheDocument();
});

test("an old ?tab=preview link opens the editor on Preview", async () => {
  const router = show("/documents/doc-1?tab=preview");
  await waitFor(() => expect(where(router)).toBe("/documents/doc-1"));
  expect(await screen.findByRole("tab", { name: "Preview", selected: true })).toBeInTheDocument();
});

test("an old ?tab=resources link of a live document opens Connections, where its links are", async () => {
  const router = show("/documents/doc-1?tab=resources");
  await waitFor(() => expect(where(router)).toBe("/documents/doc-1/connections"));
  expect(await screen.findByText("Not linked to any resource yet.")).toBeInTheDocument();
});

test("unsaved edits stay through the sub-pages without asking; leaving the page asks Stay / Leave", async () => {
  const user = userEvent.setup();
  const router = show("/documents/doc-1");
  fireEvent.change(await screen.findByLabelText("Markdown"), { target: { value: "# Not saved yet" } });
  await user.click(nav().getByRole("link", { name: "Version history" }));
  expect(await screen.findByText(/Every published version/)).toBeVisible();
  expect(screen.queryByRole("alertdialog")).toBeNull();
  await user.click(nav().getByRole("link", { name: "Connections" }));
  await waitFor(() => expect(where(router)).toBe("/documents/doc-1/connections"));
  expect(screen.queryByRole("alertdialog")).toBeNull();
  await user.click(nav().getByRole("link", { name: "Edit" }));
  expect(await screen.findByLabelText("Markdown")).toHaveValue("# Not saved yet");
  expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
  await user.click(screen.getByRole("link", { name: "All documents" }));
  const dialog = within(await screen.findByRole("alertdialog"));
  await user.click(dialog.getByRole("button", { name: "Stay" }));
  expect(where(router)).toBe("/documents/doc-1");
  await user.click(screen.getByRole("link", { name: "All documents" }));
  await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Leave" }));
  expect(await screen.findByText("All documents list")).toBeInTheDocument();
});

test("the section links keep the open run (?run=)", async () => {
  show("/documents/doc-1/history?run=r1");
  expect(await screen.findByText("run r1")).toBeInTheDocument();
  expect(nav().getByRole("link", { name: "Drafts (1)" })).toHaveAttribute("href", "/documents/doc-1/drafts?run=r1");
});

test.each([
  ["/history", /Select a version/],
  ["/drafts", /Runbook, shorter/],
  ["/connections", /Find a document/],
  ["/agent-runs", /No agent runs yet/],
])("every button on the sub-page %s is type=button: none of them submits the editor's form", async (page, loaded) => {
  show(`/documents/doc-1${page}`);
  await screen.findAllByText(loaded);
  const area = globalThis.document.querySelector("fieldset")!;
  for (const button of area.querySelectorAll("button")) expect(button).toHaveAttribute("type", "button");
});
