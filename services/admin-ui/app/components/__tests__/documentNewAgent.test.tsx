import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import routes from "~/routes";
import DocumentEditorView from "~/features/documents/DocumentEditorView";
import NewDocumentAgent from "~/features/documents/NewDocumentAgent";
import NewDocumentLayout from "~/features/documents/NewDocumentLayout";
import { PICK_TO_WRITE } from "~/features/sessions/agentSettings";
import { WidePageContext } from "~/lib/widePage";
import { options } from "~/lib/__tests__/agentOptionsFixture";

vi.mock("../Markdown", () => ({ default: ({ content }: { content: string }) => <article>{content}</article> }));
const live = vi.hoisted(() => ({
  sessions: {} as Record<string, any>,
  setEvents: null as null | ((events: any[]) => void),
}));
vi.mock("~/features/sessions/useSessionEvents", async () => {
  const { useState } = await import("react");
  return {
    useSessionEvents: (id: string | null) => {
      const [error, setError] = useState("");
      const [events, setEvents] = useState<any[]>([]);
      live.setEvents = setEvents;
      return {
        session: id ? (live.sessions[id] ?? null) : null,
        setSession: vi.fn(),
        error,
        setError,
        events,
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
        <textarea ref={composer.inputRef} value={composer.value} onChange={(e) => composer.onChange(e.target.value)} />
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
  live.setEvents = null;
});

const at = "2026-10-27T10:00:00Z";
const resources = {
  code_projects: [],
  services: [],
  servers: [{ id: "s1", name: "web-1" }],
  deployments: [],
  environments: [],
};
const writing = { ...options, resources: { ...options.resources, documents: [] } };
const unlinked = {
  ...writing,
  resources: {
    ...writing.resources,
    servers: writing.resources.servers.map((item) => ({ ...item, linked: false })),
    services: writing.resources.services.map((item) => ({ ...item, linked: false })),
  },
};
const run = (id: string, title: string) => ({
  id,
  title,
  status: "waiting",
  purpose: "documents",
  tags: [],
  model_name: "GPT",
  started_by: null,
  created_at: at,
  last_activity_at: at,
  draft_id: null,
});
const detail = (id: string, tags: string[] = []) => ({
  id,
  title: `Write ${id}`,
  status: "waiting",
  purpose: "documents",
  tags,
  target_document_id: null,
  agent_name: "Ad-hoc agent",
  model_name: "GPT",
  turns: 1,
  config: {},
  document_draft: null,
});
const drafts = [
  {
    id: "d1",
    title: "Disk layout",
    draft_of: null,
    draft_of_title: null,
    state: "open",
    updated_at: at,
  },
  {
    id: "d2",
    title: "Runbook",
    draft_of: "doc-1",
    draft_of_title: "Runbook",
    state: "open",
    updated_at: at,
  },
  { id: "d3", title: "Backups", draft_of: null, draft_of_title: null, state: "published", updated_at: at },
];
type Routes = Record<string, (body: any) => [number, unknown]>;
function api(routes: Routes) {
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
const plain: Routes = {
  "GET /api/sessions/options?new_document=1": () => [200, writing],
  "GET /api/sessions?purpose=documents": () => [200, { sessions: [] }],
};
const onServer: Routes = {
  "GET /api/sessions/options?server_id=s1&new_document=1": () => [200, writing],
  "GET /api/sessions?tag=servers%2Fs1&purpose=documents": () => [200, { sessions: [] }],
};
const wide = { value: false };
function show(url: string) {
  const router = createMemoryRouter(
    [
      {
        path: "/documents/new",
        element: <NewDocumentLayout />,
        children: [
          { index: true, element: <DocumentEditorView /> },
          { path: "agent", element: <NewDocumentAgent /> },
        ],
      },
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
const chat = () => screen.getByRole("region", { name: "Ask agent" });
const documents = () => screen.getByRole("region", { name: "Documents in this chat" });

test("routes: the New document page has Write and Write with an agent as sub-pages", () => {
  const page = routes.find((item) => item.path === "documents/new")!;
  expect(page.file).toBe("./routes/documentNew.tsx");
  expect(page.children?.map((item) => [item.index ?? false, item.path, item.file])).toEqual([
    [true, undefined, "./routes/documentNewWrite.tsx"],
    [false, "agent", "./routes/documentNewAgent.tsx"],
  ]);
});

test("Write is the form alone; the tabs keep the resource and lead to Write with an agent", async () => {
  api({ "GET /api/documentation/resources": () => [200, resources], ...onServer });
  const router = show("/documents/new?resource=server&resource_id=s1");
  const tabs = screen.getByRole("navigation", { name: "New document" });
  const write = within(tabs).getByRole("link", { name: "Write" });
  const agent = within(tabs).getByRole("link", { name: "Write with an agent" });
  expect(write).toHaveAttribute("href", "/documents/new?resource=server&resource_id=s1");
  expect(write).toHaveAttribute("aria-current", "page");
  expect(agent).toHaveAttribute("href", "/documents/new/agent?resource=server&resource_id=s1");
  expect(await screen.findByLabelText("Title")).toBeInTheDocument();
  expect(screen.getByText("New document for server web-1.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Write with an agent" })).toBeNull(); // no agent toggle on the form
  expect(screen.queryByRole("region", { name: "Ask agent" })).toBeNull();
  fireEvent.click(agent);
  await waitFor(() => expect(router.state.location.pathname).toBe("/documents/new/agent"));
  expect(router.state.location.search).toBe("?resource=server&resource_id=s1");
  expect(await screen.findByRole("region", { name: "Ask agent" })).toBeInTheDocument();
  expect(within(tabs).getByRole("link", { name: "Write with an agent" })).toHaveAttribute("aria-current", "page");
});

test("without a resource the chat needs a picked resource, then starts writing from the picked ones", async () => {
  const calls = api({ ...plain, "GET /api/sessions/options?new_document=1": () => [200, unlinked] });
  show("/documents/new/agent");
  expect(
    await within(await screen.findByRole("region", { name: "Ask agent" })).findByText(PICK_TO_WRITE),
  ).toBeVisible();
  expect(wide.value).toBe(true);
  expect(screen.getByTestId("write-layout")).toHaveClass("lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]");
  expect(within(documents()).getByText("The documents the agent writes in this conversation show here.")).toBeVisible();
  fireEvent.change(within(chat()).getByLabelText("Message"), { target: { value: "Write it." } });
  fireEvent.click(within(chat()).getByRole("button", { name: "Send message" }));
  expect(calls.some((c) => c.key === "POST /api/sessions")).toBe(false);
  expect(within(chat()).queryByRole("button", { name: "New chat" })).toBeNull(); // the page has New chat
  expect(screen.getAllByRole("button", { name: "New chat" })).toHaveLength(1);
});

test("the first message starts writing with started_from new_document and the picked resources", async () => {
  const calls = api({
    ...plain,
    "POST /api/sessions": () => {
      live.sessions.r1 = detail("r1");
      return [201, live.sessions.r1];
    },
    "GET /api/documentation/drafts?session_id=r1": () => [200, { drafts: [] }],
  });
  const router = show("/documents/new/agent");
  const pane = await screen.findByRole("region", { name: "Ask agent" });
  await waitFor(() => expect(within(pane).queryByText(PICK_TO_WRITE)).toBeNull());
  fireEvent.change(within(pane).getByLabelText("Message"), { target: { value: "Write it." } });
  await waitFor(() => expect(within(pane).getByRole("button", { name: "Send message" })).toBeEnabled());
  fireEvent.click(within(pane).getByRole("button", { name: "Send message" }));
  await waitFor(() =>
    expect(calls.find((c) => c.key === "POST /api/sessions")?.body).toMatchObject({
      started_from: { new_document: true },
      message: "Write it.",
      context: "",
      resources: { server_ids: ["s1"], service_ids: ["v1"], documents: [] },
    }),
  );
  await waitFor(() => expect(router.state.location.search).toBe("?chat=r1"));
  expect(
    await within(documents()).findByText("The agent has not written a document in this conversation yet."),
  ).toBeVisible();
});

test("on a resource the chat is on it: started_from carries the resource", async () => {
  const calls = api({
    ...onServer,
    "POST /api/sessions": () => {
      live.sessions.r1 = detail("r1", ["servers/s1"]);
      return [201, live.sessions.r1];
    },
    "GET /api/documentation/drafts?session_id=r1": () => [200, { drafts: [] }],
  });
  const router = show("/documents/new/agent?resource=server&resource_id=s1");
  const pane = await screen.findByRole("region", { name: "Ask agent" });
  fireEvent.change(within(pane).getByLabelText("Message"), { target: { value: "Write the runbook." } });
  await waitFor(() => expect(within(pane).queryByText(PICK_TO_WRITE)).toBeNull());
  fireEvent.click(within(pane).getByRole("button", { name: "Send message" }));
  await waitFor(() =>
    expect(calls.find((c) => c.key === "POST /api/sessions")?.body).toMatchObject({
      started_from: { new_document: true, server_id: "s1" },
      resources: { server_ids: ["s1"], documents: [] },
    }),
  );
  await waitFor(() => expect(router.state.location.search).toBe("?resource=server&resource_id=s1&chat=r1"));
});

test("Documents in this chat: titles open in a new tab, New or Edit of, the state, and Mention puts the reference at the cursor", async () => {
  live.sessions.r1 = detail("r1");
  api({ ...plain, "GET /api/documentation/drafts?session_id=r1": () => [200, { drafts }] });
  show("/documents/new/agent?chat=r1");
  const list = await within(await screen.findByRole("region", { name: "Documents in this chat" })).findByRole("list", {
    name: "Documents",
  });
  const items = within(list).getAllByRole("listitem");
  const disk = within(items[0]).getByRole("link", { name: "Disk layout" });
  expect(disk).toHaveAttribute("href", "/documents/drafts/d1");
  expect(disk).toHaveAttribute("target", "_blank");
  expect(disk).toHaveAttribute("rel", "noopener");
  expect(items[0]).toHaveTextContent("New");
  expect(items[0]).toHaveTextContent("Draft");
  expect(items[1]).toHaveTextContent("Edit of Runbook");
  const [edit, original] = within(items[1]).getAllByRole("link", { name: "Runbook" });
  expect(edit).toHaveAttribute("href", "/documents/drafts/d2");
  expect(original).toHaveAttribute("href", "/documents/doc-1");
  expect(original).toHaveAttribute("target", "_blank");
  expect(within(items[2]).getByRole("link", { name: "Backups" })).toHaveAttribute("href", "/documents/d3");
  expect(items[2]).toHaveTextContent("Published");

  const box = within(chat()).getByLabelText("Message") as HTMLTextAreaElement;
  fireEvent.change(box, { target: { value: "Fix  please" } });
  box.setSelectionRange(4, 4);
  fireEvent.click(within(items[0]).getByRole("button", { name: "Mention Disk layout" }));
  await waitFor(() => expect(box.value).toBe('Fix [Draft "Disk layout" · d1] please'));
  box.setSelectionRange(box.value.length, box.value.length);
  fireEvent.click(within(items[1]).getByRole("button", { name: "Mention Runbook" }));
  await waitFor(() => expect(box.value).toBe('Fix [Draft "Disk layout" · d1] please [Draft "Runbook" · d2]'));
});

test("the list loads again when a document write's result arrives in the conversation's events", async () => {
  live.sessions.r1 = detail("r1");
  let listed = [drafts[0]];
  const calls = api({ ...plain, "GET /api/documentation/drafts?session_id=r1": () => [200, { drafts: listed }] });
  show("/documents/new/agent?chat=r1");
  await within(await screen.findByRole("region", { name: "Documents in this chat" })).findByText("Disk layout");
  const loads = () => calls.filter((c) => c.key === "GET /api/documentation/drafts?session_id=r1").length;
  const before = loads();
  const event = (id: number, kind: string, payload: Record<string, unknown>) => ({
    id,
    turn: 2,
    seq: id,
    kind,
    payload,
    created_at: at,
  });
  act(() =>
    live.setEvents!([
      event(1, "tool_call", { name: "get_document", call_id: "a" }),
      event(2, "tool_result", { call_id: "a", preview: "{}" }),
    ]),
  );
  await new Promise((resolve) => setTimeout(resolve, 20));
  expect(loads()).toBe(before); // a read writes nothing
  listed = drafts;
  act(() =>
    live.setEvents!([
      event(1, "tool_call", { name: "get_document", call_id: "a" }),
      event(2, "tool_result", { call_id: "a", preview: "{}" }),
      event(3, "tool_call", { name: "new_document", call_id: "b" }),
      event(4, "tool_result", { call_id: "b", preview: "Created the draft" }),
    ]),
  );
  expect(await within(documents()).findByText("Backups")).toBeVisible();
  expect(loads()).toBe(before + 1);
});

test("Conversations lists the earlier Write with an agent conversations; opening one shows its documents", async () => {
  live.sessions.r1 = detail("r1", ["servers/s1"]);
  live.sessions.r2 = detail("r2", ["servers/s1"]);
  api({
    ...onServer,
    "GET /api/sessions?tag=servers%2Fs1&purpose=documents": () => [
      200,
      { sessions: [run("r2", "Write the backups"), run("r1", "Write the runbook")] },
    ],
    "GET /api/documentation/drafts?session_id=r1": () => [200, { drafts: [drafts[0]] }],
    "GET /api/documentation/drafts?session_id=r2": () => [200, { drafts: [drafts[2]] }],
  });
  const router = show("/documents/new/agent?resource=server&resource_id=s1&chat=r1");
  expect(
    await within(await screen.findByRole("region", { name: "Documents in this chat" })).findByText("Disk layout"),
  ).toBeVisible();
  expect(await screen.findByTestId("conversation-title")).toHaveTextContent("Write the runbook");
  fireEvent.click(screen.getByRole("button", { name: "Conversations" }));
  const sheet = await screen.findByRole("dialog");
  expect(within(sheet).getByText("Every Write with an agent conversation about web-1, newest first.")).toBeVisible();
  const listed = within(sheet).getAllByRole("button", { name: /Write the/ });
  expect(listed.map((item) => item.textContent)).toEqual([
    expect.stringContaining("Write the backups"),
    expect.stringContaining("Write the runbook"),
  ]);
  expect(listed[1]).toHaveAttribute("aria-current", "true");
  fireEvent.click(listed[0]);
  await waitFor(() => expect(router.state.location.search).toBe("?resource=server&resource_id=s1&chat=r2"));
  expect(await within(documents()).findByText("Backups")).toBeVisible();
  expect(within(documents()).queryByText("Disk layout")).toBeNull();
});

test("after a Mention, New chat starts with an empty message box", async () => {
  live.sessions.r1 = detail("r1");
  api({ ...plain, "GET /api/documentation/drafts?session_id=r1": () => [200, { drafts }] });
  show("/documents/new/agent?chat=r1");
  const list = await within(await screen.findByRole("region", { name: "Documents in this chat" })).findByRole("list", {
    name: "Documents",
  });
  fireEvent.click(within(list).getByRole("button", { name: "Mention Disk layout" }));
  await waitFor(() =>
    expect((within(chat()).getByLabelText("Message") as HTMLTextAreaElement).value).toContain("· d1]"),
  );
  fireEvent.click(screen.getByRole("button", { name: "New chat" }));
  await waitFor(() => expect(screen.getByTestId("conversation-title")).toHaveTextContent("New conversation"));
  await new Promise((resolve) => setTimeout(resolve, 20));
  expect((within(chat()).getByLabelText("Message") as HTMLTextAreaElement).value).toBe("");
});

test("opening another conversation clears the old documents at once while the new ones load", async () => {
  live.sessions.r1 = detail("r1");
  live.sessions.r2 = detail("r2");
  let answer: (value: Response) => void = () => {};
  api({
    ...plain,
    "GET /api/sessions?purpose=documents": () => [200, { sessions: [run("r2", "Write the backups")] }],
    "GET /api/documentation/drafts?session_id=r1": () => [200, { drafts: [drafts[0]] }],
  });
  const fetchMock = globalThis.fetch as ReturnType<typeof vi.fn>;
  const inner = fetchMock.getMockImplementation()!;
  fetchMock.mockImplementation((url: string, init?: RequestInit) =>
    url === "/api/documentation/drafts?session_id=r2"
      ? new Promise<Response>((resolve) => (answer = resolve))
      : inner(url, init),
  );
  show("/documents/new/agent?chat=r1");
  expect(
    await within(await screen.findByRole("region", { name: "Documents in this chat" })).findByText("Disk layout"),
  ).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Conversations" }));
  fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: /Write the backups/ }));
  await waitFor(() => expect(within(documents()).queryByText("Disk layout")).toBeNull());
  expect(within(documents()).queryByRole("button", { name: /Mention/ })).toBeNull();
  await act(async () => answer(new Response(JSON.stringify({ drafts: [drafts[2]] }), { status: 200 })));
  expect(await within(documents()).findByText("Backups")).toBeVisible();
});

test("the Write with an agent tab keeps the open conversation", async () => {
  live.sessions.r1 = detail("r1", ["servers/s1"]);
  api({ ...onServer, "GET /api/documentation/drafts?session_id=r1": () => [200, { drafts: [] }] });
  const router = show("/documents/new/agent?resource=server&resource_id=s1&chat=r1");
  const tabs = screen.getByRole("navigation", { name: "New document" });
  expect(within(tabs).getByRole("link", { name: "Write with an agent" })).toHaveAttribute(
    "href",
    "/documents/new/agent?resource=server&resource_id=s1&chat=r1",
  );
  expect(within(tabs).getByRole("link", { name: "Write" })).toHaveAttribute(
    "href",
    "/documents/new?resource=server&resource_id=s1",
  );
  await within(await screen.findByRole("region", { name: "Documents in this chat" })).findByText(
    "The agent has not written a document in this conversation yet.",
  );
  expect(router.state.location.search).toContain("chat=r1");
});
