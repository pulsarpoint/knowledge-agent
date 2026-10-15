import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import DocumentEditorView from "~/features/documents/DocumentEditorView";
import { WidePageContext } from "~/lib/widePage";
import { options } from "~/lib/__tests__/agentOptionsFixture";

vi.mock("../Markdown", () => ({ default: ({ content }: { content: string }) => <article>{content}</article> }));
const live = vi.hoisted(() => ({ sessions: {} as Record<string, any>, requested: [] as (string | null)[] }));
vi.mock("~/features/sessions/useSessionEvents", async () => {
  const { useState } = await import("react");
  return {
    useSessionEvents: (id: string | null) => {
      live.requested.push(id);
      const [error, setError] = useState("");
      return {
        session: id ? (live.sessions[id] ?? null) : null,
        setSession: vi.fn(),
        error,
        setError,
        events: [],
        older: "start",
        loadOlder: vi.fn(),
        toolCounts: undefined,
        reconnecting: false,
      };
    },
    useSessionNotice: () => ({ notice: null, clear: vi.fn(), stopping: vi.fn(), sent: vi.fn() }),
  };
});
vi.mock("~/features/sessions/RunWorkspace", () => ({
  default: ({ heading, headerActions, empty, composer }: Record<string, any>) => (
    <div>
      {heading}
      {headerActions}
      {empty}
      <label>
        Message
        <textarea value={composer.value} onChange={(e) => composer.onChange(e.target.value)} />
      </label>
      {composer.blockedReason && <p>{composer.blockedReason}</p>}
      <button type="button" onClick={composer.onSend}>
        Send message
      </button>
    </div>
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
afterEach(() => {
  vi.unstubAllGlobals();
  live.sessions = {};
  live.requested = [];
});

const at = "2026-10-13T10:00:00Z";
const resources = { code_projects: [], services: [], servers: [], deployments: [], environments: [] };
const fresh = { ...options, resources: { ...options.resources, documents: [] } };
const detail = (draft: unknown = null) => ({
  id: "r1",
  title: "Write Payments API",
  status: "waiting",
  purpose: "documents",
  folder: "payments",
  document_name: "Payments API",
  target_document_id: null,
  agent_name: "Ad-hoc agent",
  model_name: "Claude",
  turns: 1,
  config: {},
  document_draft: draft,
});
const draft = {
  id: "d9",
  document_id: null,
  slug: "payments-api",
  folder: "payments",
  title: "Payments API",
  origin: "agent",
  session_id: "r1",
  session_title: "x",
  session_purpose: "documents",
  author: null,
  edited: false,
  revision: 1,
  base_version: null,
  live_version: null,
  stale: false,
  created_at: at,
  updated_at: at,
  description: "",
  purpose: "explanation",
  audience: "both",
  tags: [],
  markdown: "# Payments API",
  resource_ids: {},
  live_markdown: null,
  document_title: null,
  sources: [],
};
const base: Record<string, (body: any) => [number, unknown]> = {
  "GET /api/documentation/resources": () => [200, resources],
  "GET /api/sessions/options?new_document=1": () => [200, fresh],
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
const wide = { value: false };
function show(url = "/documents/new") {
  const router = createMemoryRouter(
    [
      { path: "/documents/new", element: <DocumentEditorView /> },
      { path: "/documents/drafts/:draftId", element: <p>draft page</p> },
      { path: "/documents/:documentId", element: <p>document page</p> },
    ],
    { initialEntries: [url] },
  );
  render(
    <WidePageContext.Provider
      value={(on) => {
        wide.value = on;
      }}
    >
      <RouterProvider router={router} />
    </WidePageContext.Provider>,
  );
  return router;
}
async function openPane() {
  const toggle = await screen.findByRole("button", { name: "Write with an agent" });
  expect(toggle).toHaveAttribute("aria-pressed", "false");
  fireEvent.click(toggle);
  expect(toggle).toHaveAttribute("aria-pressed", "true");
  return screen.findByRole("region", { name: "Ask agent" });
}

test("Write with an agent opens the pane beside the editor; without a title and folder nothing starts", async () => {
  const calls = api(base);
  show();
  const pane = await openPane();
  expect(wide.value).toBe(true);
  expect(screen.getByTestId("document-split")).toHaveClass("lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]");
  expect(await within(pane).findByText("Give the document a title and folder first.")).toBeInTheDocument();
  expect(within(pane).queryByRole("button", { name: "Earlier runs" })).toBeNull();
  fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Payments API" } });
  expect(within(pane).getByText("Give the document a title and folder first.")).toBeInTheDocument(); // the folder is still empty
  fireEvent.change(within(pane).getByLabelText("Message"), { target: { value: "Write it." } });
  fireEvent.click(within(pane).getByRole("button", { name: "Send message" }));
  expect(calls.some((c) => c.key === "POST /api/sessions")).toBe(false);
  expect(calls.some((c) => c.key.startsWith("GET /api/sessions?"))).toBe(false); // one conversation per visit: no run list
  // the settings sheet speaks of the new document and lists no Documents group; the settings are remembered once for all new documents
  fireEvent.click(within(pane).getByRole("button", { name: "Settings" }));
  const sheet = await screen.findByRole("dialog");
  expect(within(sheet).getByText("What the agent may use for “Payments API”.")).toBeInTheDocument();
  expect(
    within(within(sheet).getByRole("group", { name: "Servers and resources" })).queryByText("Documents"),
  ).toBeNull();
  expect(JSON.parse(localStorage.getItem("agent-settings:new-document") ?? "null")).toMatchObject({ v: 2 });
  expect(calls.filter((c) => c.key === "GET /api/sessions/options?new_document=1")).toHaveLength(1); // not per typed title
});

test("the first message starts the run with the title and folder as the new document", async () => {
  const calls = api({
    ...base,
    "POST /api/sessions": () => {
      live.sessions.r1 = detail();
      return [201, detail()];
    },
    "POST /api/sessions/r1/close": () => [200, { id: "r1" }],
  });
  show();
  const pane = await openPane();
  fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Payments API" } });
  fireEvent.change(screen.getByLabelText("Folder"), { target: { value: "payments" } });
  await waitFor(() => expect(within(pane).queryByText("Give the document a title and folder first.")).toBeNull());
  fireEvent.change(within(pane).getByLabelText("Message"), { target: { value: "Write it." } });
  fireEvent.click(within(pane).getByRole("button", { name: "Send message" }));
  await waitFor(() =>
    expect(calls.find((c) => c.key === "POST /api/sessions")?.body).toMatchObject({
      started_from: { new_document: { folder: "payments", name: "Payments API" } },
      message: "Write it.",
      context: "",
      resources: { documents: [{ new: { folder: "payments", name: "Payments API" }, editable: true }] },
    }),
  );
  expect(await within(pane).findByText("Writing: payments/Payments API")).toBeInTheDocument();
  // changing the fields does not change the open run
  fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Other" } });
  expect(within(pane).getByText("Writing: payments/Payments API")).toBeInTheDocument();
  fireEvent.click(within(pane).getByRole("button", { name: "New chat" }));
  await waitFor(() => expect(calls.some((c) => c.key === "POST /api/sessions/r1/close")).toBe(true));
  await waitFor(() => expect(live.requested.at(-1)).toBeNull());
  expect(within(pane).queryByText("Writing: payments/Payments API")).toBeNull();
});

test("on the New inventory document page the run writes the resource's inventory document", async () => {
  const calls = api({
    ...base,
    "POST /api/sessions": () => {
      live.sessions.r1 = detail();
      return [201, detail()];
    },
  });
  show("/documents/new?inventory=server&resource_id=s1");
  const pane = await openPane();
  fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Payments API" } });
  fireEvent.change(screen.getByLabelText("Folder"), { target: { value: "payments" } });
  await waitFor(() => expect(within(pane).queryByText("Give the document a title and folder first.")).toBeNull());
  fireEvent.change(within(pane).getByLabelText("Message"), { target: { value: "Write it." } });
  fireEvent.click(within(pane).getByRole("button", { name: "Send message" }));
  const written = { folder: "payments", name: "Payments API", inventory: true, server_id: "s1" };
  await waitFor(() =>
    expect(calls.find((c) => c.key === "POST /api/sessions")?.body).toMatchObject({
      started_from: { new_document: written },
      resources: { documents: [{ new: written, editable: true }] },
    }),
  );
});

test("the agent draft shows above the editor; Promote opens the new document", async () => {
  const calls = api({
    ...base,
    "POST /api/sessions": () => {
      live.sessions.r1 = detail({ id: "d9", document_id: null, status: "open" });
      return [201, live.sessions.r1];
    },
    "GET /api/documentation/drafts/d9": () => [200, draft],
    "POST /api/documentation/drafts/d9/promote": () => [
      200,
      { document_id: "d9", version: 1, revision: 2, deleted_drafts: 0, export_error: null },
    ],
  });
  const router = show();
  const pane = await openPane();
  fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Payments API" } });
  fireEvent.change(screen.getByLabelText("Folder"), { target: { value: "payments" } });
  fireEvent.change(within(pane).getByLabelText("Message"), { target: { value: "Write it." } });
  await waitFor(() => expect(within(pane).queryByText("Give the document a title and folder first.")).toBeNull());
  fireEvent.click(within(pane).getByRole("button", { name: "Send message" }));
  const bar = await screen.findByRole("region", { name: "Agent draft" });
  await waitFor(() => expect(within(bar).getByRole("button", { name: "Promote" })).toBeEnabled());
  fireEvent.click(within(bar).getByRole("button", { name: "Promote" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/documents/d9"));
  expect(calls.find((c) => c.key === "POST /api/documentation/drafts/d9/promote")?.body).toEqual({
    revision: 1,
    confirm_stale: false,
  });
});

test.each([
  ["a new document", { document_id: null }, "/documents/d9", ""],
  [
    "an existing document (its title and folder named one)",
    { document_id: "doc-1", base_version: 1, live_version: 1 },
    "/documents/doc-1",
    "?tab=drafts&draft=d9",
  ],
])("Compare on the New page opens the draft of %s", async (_, shape, pathname, search) => {
  const shaped = { ...draft, ...shape };
  api({
    ...base,
    "POST /api/sessions": () => {
      live.sessions.r1 = detail({ id: "d9", document_id: shaped.document_id, status: "open" });
      return [201, live.sessions.r1];
    },
    "GET /api/documentation/drafts/d9": () => [200, shaped],
  });
  const router = show();
  const pane = await openPane();
  fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Payments API" } });
  fireEvent.change(screen.getByLabelText("Folder"), { target: { value: "payments" } });
  fireEvent.change(within(pane).getByLabelText("Message"), { target: { value: "Write it." } });
  await waitFor(() => expect(within(pane).queryByText("Give the document a title and folder first.")).toBeNull());
  fireEvent.click(within(pane).getByRole("button", { name: "Send message" }));
  const bar = await screen.findByRole("region", { name: "Agent draft" });
  fireEvent.click(within(bar).getByRole("button", { name: "Compare" }));
  // the typed title and folder are unsaved edits: leaving the page asks first, as any link away does
  fireEvent.click(await screen.findByRole("button", { name: "Discard changes" }));
  await waitFor(() => expect(router.state.location.pathname).toBe(pathname));
  expect(router.state.location.search).toBe(search);
});
