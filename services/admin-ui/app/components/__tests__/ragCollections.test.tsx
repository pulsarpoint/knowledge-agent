import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import RagCollectionsView from "~/features/rag/RagCollectionsView";
import RagCollectionDetail from "~/features/rag/RagCollectionDetail";
import { pollIntervalToSend } from "~/features/rag/RagCollectionForm";
import type { CollectionPath, RagCollection } from "~/features/rag/api";

const jiraPath: CollectionPath = {
  id: "p1",
  collection_id: "c1",
  source_path: "jira",
  created_at: "2026-10-05T09:00:00Z",
  files: 75,
  host_path: "/opt/ka/jira",
  container_path: "/work/data/jira",
  present: true,
};
const codePath: CollectionPath = {
  ...jiraPath,
  id: "p2",
  source_path: "code/payments",
  files: 12,
  host_path: "/opt/ka/code/payments",
  container_path: "/work/code/payments",
};
const notesPath: CollectionPath = {
  ...jiraPath,
  id: "p3",
  source_path: "notes",
  files: 3,
  host_path: "/opt/ka/notes",
  container_path: "/work/data/notes",
};
const base: RagCollection = {
  id: "c1",
  name: "jira",
  description: "Jira tickets",
  search_mode: "full_text",
  embeddings_enabled: true,
  include: ["**/*.md"],
  exclude: [],
  chunk_size: 2000,
  chunk_overlap: 200,
  auto_sync: false,
  poll_interval_minutes: 5,
  last_polled_at: null,
  revision: 2,
  synced_revision: 2,
  last_synced_at: "2026-10-05T09:00:00Z",
  paths: [codePath, jiraPath, notesPath],
  folder_present: true,
  missing_paths: [],
  folder_changed_at: "2026-10-05T10:00:00Z",
  file_count: 90,
  truncated: false,
  too_large: false,
  skipped: { links: 0, special: 0, unreadable: 0, invalid_names: 0 },
  log_counts: { indexed: 90, skipped: 2, failed: 1 },
  indexed_files: 90,
  chunks: 975,
  embedded_chunks: 0,
  needs_refresh: true,
  status: "needs_refresh",
  job: null,
};
const notes: RagCollection = { ...base, id: "c2", name: "notes", paths: [notesPath] };
const noLog = { files: [], total: 0 };

function detail(fetchMock: ReturnType<typeof vi.fn>) {
  vi.stubGlobal("fetch", fetchMock);
  return render(
    <MemoryRouter initialEntries={["/rag/collections/c1"]}>
      <Routes>
        <Route path="/rag/collections/:id" element={<RagCollectionDetail id="c1" />} />
        <Route path="/rag/collections" element={<div>collections page</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

test("lists collections with their folders and no source badges", async () => {
  const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === "POST") return Response.json({ id: "job" }, { status: 202 });
    return Response.json({ collections: [base, { ...notes, status: "up_to_date", needs_refresh: false }] });
  });
  vi.stubGlobal("fetch", fetchMock);
  render(
    <MemoryRouter>
      <RagCollectionsView />
    </MemoryRouter>,
  );
  const row = (await screen.findByRole("link", { name: "jira" })).closest("tr")!;
  expect(within(row).getByText("/opt/ka/jira")).toBeInTheDocument();
  expect(within(row).getByText("75 files")).toBeInTheDocument();
  expect(within(row).getByText("0 / 975")).toBeInTheDocument();
  expect(screen.queryByText("Unowned")).toBeNull();
  expect(screen.queryByText(/Code project/)).toBeNull();
  expect(fetchMock.mock.calls.some(([url]) => String(url).startsWith("/api/code-projects"))).toBe(false);
  const notesRow = screen.getByRole("link", { name: "notes" }).closest("tr")!;
  expect(within(notesRow).queryByRole("button", { name: "Refresh" })).toBeNull();
  await userEvent.click(within(row).getByRole("button", { name: "Refresh" }));
  await waitFor(() =>
    expect(fetchMock).toHaveBeenCalledWith("/api/rag/collections/c1/sync", expect.objectContaining({ method: "POST" })),
  );
});

test("New collection creates a collection and opens it", async () => {
  const fetchMock = vi.fn(async (_url: string, init?: RequestInit) =>
    init?.method === "POST"
      ? Response.json({ ...base, id: "c9", name: "payments", paths: [] }, { status: 201 })
      : Response.json({ collections: [] }),
  );
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  render(
    <MemoryRouter initialEntries={["/rag/collections"]}>
      <Routes>
        <Route path="/rag/collections" element={<RagCollectionsView />} />
        <Route path="/rag/collections/:id" element={<div>detail page</div>} />
      </Routes>
    </MemoryRouter>,
  );
  expect(await screen.findByText(/No collections yet/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "New collection" }));
  await user.type(screen.getByLabelText("Name"), "payments");
  await user.click(screen.getByRole("button", { name: "Create collection" }));
  expect(await screen.findByText("detail page")).toBeInTheDocument();
  const post = fetchMock.mock.calls.find(([, init]) => (init as RequestInit)?.method === "POST")!;
  expect(post[0]).toBe("/api/rag/collections");
  expect(JSON.parse((post[1] as RequestInit).body as string)).toEqual({
    name: "payments",
    description: "",
    search_mode: "full_text",
    embeddings_enabled: false,
    include: ["**/*.md", "**/*.markdown", "**/*.txt"],
    exclude: [],
    chunk_size: 2000,
    chunk_overlap: 200,
    auto_sync: false,
    poll_interval_minutes: 5,
  });
});

test("an invalid name disables Create collection", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ collections: [] })),
  );
  const user = userEvent.setup();
  render(
    <MemoryRouter>
      <RagCollectionsView />
    </MemoryRouter>,
  );
  await user.click(await screen.findByRole("button", { name: "New collection" }));
  await user.type(screen.getByLabelText("Name"), "Payments");
  expect(
    screen.getByText("Use lowercase letters, digits and single hyphens, up to 60 characters."),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Create collection" })).toBeDisabled();
});

test("missing folders are named and failed syncs show the error", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({
        collections: [
          {
            ...base,
            status: "folder_missing",
            folder_present: false,
            missing_paths: ["jira"],
            paths: [{ ...jiraPath, present: false }],
          },
          {
            ...notes,
            status: "failed",
            job: {
              id: "j",
              status: "failed",
              error: "Select an enabled embedding model",
              created_at: "",
              finished_at: "",
            },
          },
        ],
      }),
    ),
  );
  render(
    <MemoryRouter>
      <RagCollectionsView />
    </MemoryRouter>,
  );
  expect(await screen.findByText("Folder missing")).toBeInTheDocument();
  expect(screen.getByText("Missing: jira")).toBeInTheDocument();
  expect(screen.getByText(/Select an enabled embedding model/)).toBeInTheDocument();
});

test("a collection too large to walk says so instead of Needs refresh", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ collections: [{ ...base, too_large: true }] })),
  );
  render(
    <MemoryRouter>
      <RagCollectionsView />
    </MemoryRouter>,
  );
  expect(await screen.findByText("Too large to sync")).toBeInTheDocument();
  expect(screen.queryByText("Needs refresh")).toBeNull();
});

test("a collection whose status walk stopped but that fits the sync limits can be refreshed", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ collections: [{ ...base, truncated: true, too_large: false }] })),
  );
  render(
    <MemoryRouter>
      <RagCollectionsView />
    </MemoryRouter>,
  );
  expect(await screen.findByText("Needs refresh")).toBeInTheDocument();
  expect(screen.queryByText("Too large to sync")).toBeNull();
  expect(screen.getByRole("button", { name: "Refresh" })).toBeEnabled();
});

test("detail shows status counts, the file log and a file's chunks", async () => {
  const fetchMock = vi.fn(async (url: string) => {
    if (url.includes("/chunks"))
      return Response.json({ chunks: [{ position: 0, text: "First chunk text", embedded: false }] });
    if (url.includes("/log?"))
      return Response.json({
        total: 1,
        files: [
          {
            path: "jira/DEMO-1.md",
            status: "indexed",
            size: 10,
            mtime: 1,
            error: null,
            indexed_at: "2026-10-05T09:00:00Z",
            last_seen_at: "2026-10-05T09:00:00Z",
            removed_at: null,
            chunks: 13,
          },
        ],
      });
    return Response.json(base);
  });
  const user = userEvent.setup();
  detail(fetchMock);
  expect(await screen.findByText("90 indexed · 2 skipped · 1 failed")).toBeInTheDocument();
  const file = await screen.findByRole("button", { name: "jira/DEMO-1.md" });
  expect(within(file.closest("tr")!).getByText("13")).toBeInTheDocument();
  await user.click(file);
  expect(await screen.findByText("First chunk text")).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith(
    expect.stringContaining("/api/rag/collections/c1/chunks?path=jira%2FDEMO-1.md"),
    expect.anything(),
  );
  expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/files"))).toBe(false);
});

test("Edit changes search settings only", async () => {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.includes("/log?")) return Response.json(noLog);
    return Response.json(init?.method === "PUT" ? { ...base, revision: 3 } : base);
  });
  const user = userEvent.setup();
  detail(fetchMock);
  await user.click(await screen.findByRole("button", { name: "Edit" }));
  expect(screen.queryByLabelText("Name")).toBeNull();
  await user.clear(screen.getByLabelText("Include patterns"));
  await user.type(screen.getByLabelText("Include patterns"), "**/*.py");
  await user.click(screen.getByRole("button", { name: "Save collection" }));
  await waitFor(() => expect(fetchMock.mock.calls.some(([, i]) => (i as RequestInit)?.method === "PUT")).toBe(true));
  const put = fetchMock.mock.calls.find(([, i]) => (i as RequestInit)?.method === "PUT")!;
  expect(put[0]).toBe("/api/rag/collections/c1");
  expect(JSON.parse((put[1] as RequestInit).body as string)).toEqual({
    description: "Jira tickets",
    search_mode: "full_text",
    embeddings_enabled: true,
    include: ["**/*.py"],
    exclude: [],
    chunk_size: 2000,
    chunk_overlap: 200,
    auto_sync: false,
    poll_interval_minutes: 5,
    revision: 2,
  });
});

test("Remove collection is always available and asks for confirmation", async () => {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.includes("/log?")) return Response.json(noLog);
    if (init?.method === "DELETE") return Response.json({ deleted_files: 3 });
    return Response.json(base);
  });
  const user = userEvent.setup();
  detail(fetchMock);
  await user.click(await screen.findByRole("button", { name: "Remove" }));
  expect(screen.getByText(/files on disk are kept/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Remove collection" }));
  await waitFor(() =>
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/rag/collections/c1?revision=2&confirmed=true",
      expect.objectContaining({ method: "DELETE" }),
    ),
  );
  expect(await screen.findByText("collections page")).toBeInTheDocument();
});

test("a failed Remove collection shows the error and reloads so the retry uses the new revision", async () => {
  let revision = 2;
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "DELETE")
      return Response.json({ error: { message: "Collection changed; refresh before removing" } }, { status: 409 });
    if (url.includes("/log?")) return Response.json(noLog);
    const row = { ...base, revision };
    revision = 5;
    return Response.json(row);
  });
  const user = userEvent.setup();
  detail(fetchMock);
  await user.click(await screen.findByRole("button", { name: "Remove" }));
  await user.click(screen.getByRole("button", { name: "Remove collection" }));
  expect(await screen.findByText("Collection changed; refresh before removing")).toBeInTheDocument();
  expect(screen.queryByText("collections page")).toBeNull();
  await waitFor(() =>
    expect(
      fetchMock.mock.calls.filter(
        ([u, i]) => String(u) === "/api/rag/collections/c1" && !(i as RequestInit)?.method?.match(/DELETE/),
      ).length,
    ).toBeGreaterThan(1),
  );
  await user.click(screen.getByRole("button", { name: "Remove" }));
  await user.click(screen.getByRole("button", { name: "Remove collection" }));
  await waitFor(() =>
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/rag/collections/c1?revision=5&confirmed=true",
      expect.objectContaining({ method: "DELETE" }),
    ),
  );
});

test("Refresh is disabled for a collection too large to sync", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) =>
      url.includes("/log?") ? Response.json(noLog) : Response.json({ collections: [{ ...base, too_large: true }] }),
    ),
  );
  render(
    <MemoryRouter>
      <RagCollectionsView />
    </MemoryRouter>,
  );
  const button = await screen.findByRole("button", { name: "Refresh" });
  expect(button).toBeDisabled();
  expect(button).toHaveAttribute("title", "Over the sync limits — narrow the include patterns or folders");
  cleanup();
  detail(
    vi.fn(async (url: string) =>
      url.includes("/log?") ? Response.json(noLog) : Response.json({ ...base, too_large: true }),
    ),
  );
  const detailButton = await screen.findByRole("button", { name: "Refresh" });
  expect(detailButton).toBeDisabled();
  expect(detailButton).toHaveAttribute("title", "Over the sync limits — narrow the include patterns or folders");
});

test("Add folder connects a typed folder and Remove folder disconnects one", async () => {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.includes("/log?")) return Response.json(noLog);
    if (url.startsWith("/api/rag/folders")) return Response.json({ path: null, truncated: false, folders: [] });
    return Response.json(base);
  });
  const user = userEvent.setup();
  detail(fetchMock);
  await user.click(await screen.findByRole("button", { name: "Add folder" }));
  await user.type(await screen.findByLabelText("Folder path"), "docs/runbooks");
  await user.click(screen.getByRole("button", { name: "Connect folder" }));
  await waitFor(() =>
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/rag/collections/c1/paths",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ source_path: "docs/runbooks" }) }),
    ),
  );
  expect(await screen.findByText("Folder added. Refresh the collection to index it.")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Remove folder notes" }));
  expect(
    screen.getByText(
      "Remove folder notes from jira? Its indexed files and log entries are removed from search. Files on disk are kept.",
    ),
  ).toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([, i]) => (i as RequestInit)?.method === "DELETE")).toBe(false);
  await user.click(screen.getByRole("button", { name: "Remove folder" }));
  await waitFor(() =>
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/rag/collections/c1/paths/p3",
      expect.objectContaining({ method: "DELETE" }),
    ),
  );
  expect(await screen.findByText("Folder removed. Its files left search; files on disk are kept.")).toBeInTheDocument();
});

test("?name= opens the matching collection", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ collections: [base] })),
  );
  render(
    <MemoryRouter initialEntries={["/rag/collections?name=jira"]}>
      <Routes>
        <Route path="/rag/collections" element={<RagCollectionsView />} />
        <Route path="/rag/collections/:id" element={<div>detail page</div>} />
      </Routes>
    </MemoryRouter>,
  );
  expect(await screen.findByText("detail page")).toBeInTheDocument();
});

test("a revision conflict reloads the collection so the next edit uses the new revision", async () => {
  let revision = 2;
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "PUT")
      return Response.json({ error: { message: "Collection changed; refresh before saving" } }, { status: 409 });
    if (url.includes("/log?")) return Response.json(noLog);
    const row = { ...base, revision };
    revision = 5;
    return Response.json(row);
  });
  const user = userEvent.setup();
  detail(fetchMock);
  await user.click(await screen.findByRole("button", { name: "Edit" }));
  await user.click(screen.getByRole("button", { name: "Save collection" }));
  expect(await screen.findByText("Collection changed; refresh before saving")).toBeInTheDocument();
  await waitFor(() =>
    expect(
      fetchMock.mock.calls.filter(
        ([u, i]) => String(u).endsWith("/collections/c1") && !(i as RequestInit)?.method?.match(/PUT/),
      ).length,
    ).toBeGreaterThan(1),
  );
  await user.keyboard("{Escape}");
  await user.click(await screen.findByRole("button", { name: "Edit" }));
  await user.click(screen.getByRole("button", { name: "Save collection" }));
  await waitFor(() =>
    expect(fetchMock.mock.calls.filter(([, i]) => (i as RequestInit)?.method === "PUT")).toHaveLength(2),
  );
  const puts = fetchMock.mock.calls.filter(([, i]) => (i as RequestInit)?.method === "PUT");
  expect(JSON.parse((puts[1][1] as RequestInit).body as string).revision).toBe(5);
});

test("auto-sync settings are saved and the list shows Auto or Manual", async () => {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.includes("/log?")) return Response.json(noLog);
    if (url === "/api/rag/collections")
      return Response.json({ collections: [{ ...base, auto_sync: true, poll_interval_minutes: 15 }, notes] });
    return Response.json(init?.method === "PUT" ? { ...base, revision: 3 } : base);
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  const list = render(
    <MemoryRouter>
      <RagCollectionsView />
    </MemoryRouter>,
  );
  const auto = (await screen.findByRole("link", { name: "jira" })).closest("tr")!;
  expect(within(auto).getByText("Auto · every 15 min")).toBeInTheDocument();
  expect(within(screen.getByRole("link", { name: "notes" }).closest("tr")!).getByText("Manual")).toBeInTheDocument();
  list.unmount();
  detail(fetchMock);
  expect(await screen.findByText("Manual")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Edit" }));
  expect(screen.getByLabelText("Check every (minutes)")).toBeDisabled();
  await user.click(screen.getByRole("switch", { name: "Auto-sync" }));
  await user.clear(screen.getByLabelText("Check every (minutes)"));
  await user.type(screen.getByLabelText("Check every (minutes)"), "15");
  await user.click(screen.getByRole("button", { name: "Save collection" }));
  await waitFor(() => expect(fetchMock.mock.calls.some(([, i]) => (i as RequestInit)?.method === "PUT")).toBe(true));
  const put = fetchMock.mock.calls.find(([, i]) => (i as RequestInit)?.method === "PUT")!;
  expect(JSON.parse((put[1] as RequestInit).body as string)).toMatchObject({
    auto_sync: true,
    poll_interval_minutes: 15,
    revision: 2,
  });
});

test("an invalid check interval is never sent: the saved value while auto-sync is off, clamped while on", async () => {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.includes("/log?")) return Response.json(noLog);
    return Response.json(init?.method === "PUT" ? { ...base, revision: 3 } : { ...base, poll_interval_minutes: 30 });
  });
  const user = userEvent.setup();
  detail(fetchMock);
  const puts = () =>
    fetchMock.mock.calls
      .filter(([, i]) => (i as RequestInit)?.method === "PUT")
      .map(([, i]) => JSON.parse((i as RequestInit).body as string));
  await user.click(await screen.findByRole("button", { name: "Edit" }));
  await user.click(screen.getByRole("switch", { name: "Auto-sync" }));
  await user.clear(screen.getByLabelText("Check every (minutes)"));
  await user.click(screen.getByRole("switch", { name: "Auto-sync" }));
  await user.click(screen.getByRole("button", { name: "Save collection" }));
  await waitFor(() => expect(puts()).toHaveLength(1));
  expect(puts()[0]).toMatchObject({ auto_sync: false, poll_interval_minutes: 30 });
  await user.click(await screen.findByRole("button", { name: "Edit" }));
  await user.click(screen.getByRole("switch", { name: "Auto-sync" }));
  await user.clear(screen.getByLabelText("Check every (minutes)"));
  await user.type(screen.getByLabelText("Check every (minutes)"), "5000");
  await user.click(screen.getByRole("button", { name: "Save collection" })); // the enabled field's min/max block it
  expect(screen.getByLabelText("Check every (minutes)")).toBeInvalid();
  expect(puts()).toHaveLength(1);
  expect([
    pollIntervalToSend(5000, true, 30),
    pollIntervalToSend(0, true, 30),
    pollIntervalToSend(Number.NaN, true, 30),
  ]).toEqual([1440, 1, 30]);
  expect([pollIntervalToSend(0, false, 30), pollIntervalToSend(0, false, 0), pollIntervalToSend(7, false, 30)]).toEqual(
    [30, 5, 7],
  );
});

test("the chunk panel closes when the selected file leaves the log page or the filters change", async () => {
  const demo1 = {
    path: "jira/DEMO-1.md",
    status: "indexed",
    size: 10,
    mtime: 1,
    error: null,
    indexed_at: "2026-10-05T09:00:00Z",
    last_seen_at: "2026-10-05T09:00:00Z",
    removed_at: null,
    chunks: 1,
  };
  const demo2 = { ...demo1, path: "jira/DEMO-2.md" };
  const fetchMock = vi.fn(async (url: string) => {
    if (url.includes("/chunks"))
      return Response.json({ chunks: [{ position: 0, text: "First chunk text", embedded: true }] });
    if (url.includes("/log?"))
      return Response.json(
        url.includes("q=DEMO-2")
          ? { total: 1, files: [demo2] }
          : url.includes("offset=25")
            ? { total: 26, files: [demo2] }
            : { total: 26, files: [demo1, demo2] },
      );
    return Response.json(base);
  });
  const user = userEvent.setup();
  detail(fetchMock);
  await user.click(await screen.findByRole("button", { name: "jira/DEMO-1.md" }));
  expect(await screen.findByText("First chunk text")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Next page" })); // DEMO-1 is not on page two
  await waitFor(() => expect(screen.queryByText("First chunk text")).toBeNull());
  expect(screen.queryByText(/Chunks of/)).toBeNull();
  await user.click(await screen.findByRole("button", { name: "jira/DEMO-2.md" }));
  expect(await screen.findByText("Chunks of jira/DEMO-2.md")).toBeInTheDocument();
  await user.type(screen.getByRole("searchbox", { name: "Search files" }), "DEMO-2"); // still listed, but the search changed
  await waitFor(() => expect(screen.queryByText(/Chunks of/)).toBeNull());
});

function Opened() {
  const location = useLocation();
  return <div>detail page {(location.state as { folderError?: string } | null)?.folderError ?? ""}</div>;
}
function creating(failPaths = false) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.startsWith("/api/rag/folders"))
      return Response.json({
        path: null,
        truncated: false,
        folders: url === "/api/rag/folders" ? [{ name: "jira", path: "jira" }] : [],
      });
    if (init?.method === "POST" && url === "/api/rag/collections")
      return Response.json({ ...base, id: "c9", name: "payments-api-docs", paths: [] }, { status: 201 });
    if (init?.method === "POST" && url.endsWith("/paths"))
      return failPaths
        ? Response.json({ error: { message: "Folder does not exist" } }, { status: 409 })
        : Response.json({ ...base, id: "c9", paths: [] });
    if (init?.method === "POST" && url.endsWith("/sync")) return Response.json({ id: "job" }, { status: 202 });
    return Response.json({ collections: [] });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}
function collectionsAt(entry: string) {
  render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/rag/collections" element={<RagCollectionsView />} />
        <Route path="/rag/collections/:id" element={<Opened />} />
      </Routes>
    </MemoryRouter>,
  );
}
const writes = (fetchMock: ReturnType<typeof creating>) =>
  fetchMock.mock.calls
    .filter(([, init]) => init?.method && init.method !== "GET")
    .map(([url, init]) => [init!.method, url, JSON.parse(init!.body as string)]);

test("New collection with folders connects each folder and queues a sync right away", async () => {
  const fetchMock = creating();
  const user = userEvent.setup();
  collectionsAt("/rag/collections");
  await user.click(await screen.findByRole("button", { name: "New collection" }));
  await user.type(screen.getByLabelText("Name"), "payments-api-docs");
  await user.click(screen.getByRole("button", { name: "Choose a folder" }));
  await user.click(await screen.findByRole("button", { name: "jira" }));
  await user.click(screen.getByRole("button", { name: "Add to selection" }));
  await user.click(screen.getByRole("button", { name: "Choose a folder" }));
  await user.type(screen.getByLabelText("Folder path"), "generated-documents/payments-api-docs");
  await user.click(screen.getByRole("button", { name: "Add to selection" }));
  await user.click(screen.getByRole("button", { name: "Remove jira" }));
  await user.click(screen.getByRole("button", { name: "Choose a folder" }));
  await user.click(await screen.findByRole("button", { name: "jira" }));
  await user.click(screen.getByRole("button", { name: "Add to selection" }));
  await user.click(screen.getByRole("button", { name: "Create collection" }));
  expect(await screen.findByText("detail page")).toBeInTheDocument();
  expect(
    writes(fetchMock).map(([method, url, body]) =>
      url === "/api/rag/collections" ? [method, url, body.name] : [method, url, body],
    ),
  ).toEqual([
    ["POST", "/api/rag/collections", "payments-api-docs"],
    ["POST", "/api/rag/collections/c9/paths", { source_path: "generated-documents/payments-api-docs" }],
    ["POST", "/api/rag/collections/c9/paths", { source_path: "jira" }],
    ["POST", "/api/rag/collections/c9/sync", { force: false }],
  ]);
});

test("?folder= opens New collection with that folder selected and a name from it", async () => {
  const fetchMock = creating();
  collectionsAt("/rag/collections?folder=generated-documents%2Fpayments-api-docs");
  expect(await screen.findByLabelText("Name")).toHaveValue("payments-api-docs");
  expect(screen.getByText("generated-documents/payments-api-docs")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Remove generated-documents/payments-api-docs" })).toBeInTheDocument();
  expect(writes(fetchMock)).toEqual([]);
});

function Search() {
  return <div data-testid="search">{useLocation().search}</div>;
}
function collectionsWithSearchAt(entry: string) {
  render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route
          path="/rag/collections"
          element={
            <>
              <RagCollectionsView />
              <Search />
            </>
          }
        />
        <Route path="/rag/collections/:id" element={<Opened />} />
      </Routes>
    </MemoryRouter>,
  );
}

test("an invalid ?folder= is ignored with a notice and creates nothing", async () => {
  const fetchMock = creating();
  collectionsWithSearchAt("/rag/collections?folder=..%2Fetc");
  expect(
    await screen.findByText("The folder ../etc in the link is not a valid folder and was ignored."),
  ).toBeInTheDocument();
  expect(screen.queryByLabelText("Name")).toBeNull();
  await waitFor(() => expect(screen.getByTestId("search")).toHaveTextContent(/^$/));
  expect(writes(fetchMock)).toEqual([]);
});

test("an empty ?folder= does not open New collection", async () => {
  creating();
  collectionsWithSearchAt("/rag/collections?folder=");
  expect(await screen.findByRole("button", { name: "New collection" })).toBeInTheDocument();
  expect(screen.queryByLabelText("Name")).toBeNull();
  expect(screen.queryByRole("status")).toBeNull();
});

test("closing New collection opened from ?folder= clears it from the URL", async () => {
  creating();
  const user = userEvent.setup();
  collectionsWithSearchAt("/rag/collections?folder=generated-documents%2Fpayments-api-docs");
  expect(await screen.findByLabelText("Name")).toHaveValue("payments-api-docs");
  await user.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByLabelText("Name")).toBeNull());
  expect(screen.getByTestId("search")).toHaveTextContent(/^$/);
  await user.click(screen.getByRole("button", { name: "New collection" }));
  expect(await screen.findByLabelText("Name")).toHaveValue("");
});

test("a folder that cannot be added keeps the collection and opens it with the error", async () => {
  const fetchMock = creating(true);
  const user = userEvent.setup();
  collectionsAt("/rag/collections?folder=generated-documents%2Fpayments-api-docs");
  await user.click(await screen.findByRole("button", { name: "Create collection" }));
  expect(
    await screen.findByText(
      /Collection payments-api-docs was created, but folder generated-documents\/payments-api-docs could not be added: Folder does not exist\. Fix its folders here\./,
    ),
  ).toBeInTheDocument();
  expect(writes(fetchMock).map(([method, url]) => [method, url])).toEqual([
    ["POST", "/api/rag/collections"],
    ["POST", "/api/rag/collections/c9/paths"],
  ]); // no sync, nothing deleted
});

test("a collection opened after a failed folder shows why", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => Response.json(url.includes("/log") ? noLog : base)),
  );
  render(
    <MemoryRouter
      initialEntries={[
        {
          pathname: "/rag/collections/c1",
          state: {
            folderError:
              "Collection jira was created, but folder x could not be added: Folder does not exist. Fix its folders here.",
          },
        },
      ]}
    >
      <Routes>
        <Route path="/rag/collections/:id" element={<RagCollectionDetail id="c1" />} />
      </Routes>
    </MemoryRouter>,
  );
  expect(await screen.findByText(/folder x could not be added/)).toBeInTheDocument();
});
